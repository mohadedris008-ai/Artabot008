"""
مدیریت اتاق‌های بازی (Room Manager)
---------------------------------
نسخه‌ی قبلی این فایل فقط پیام‌های خام کلاینت را broadcast می‌کرد و هیچ
اعتبارسنجی سمت سروری روی بازی انجام نمی‌شد (یعنی موتورهای game_logic.py
مثل PasurGame/HokmGame/SequenceGame هیچ‌وقت واقعاً اجرا نمی‌شدند).

این نسخه هر اتاق را به یک نمونه‌ی واقعی از موتور بازی (GameEngineFactory)
وصل می‌کند؛ همه‌ی اکشن‌های بازیکن از مسیر engine.handle_action() عبور
می‌کنند تا قوانین بازی همیشه سمت سرور enforce بشه، نه سمت کلاینت.

سه حالتِ ورود به بازی:
  - random  : matchmaking عمومی (رفتار قبلی، بدون تغییر) — find_or_create_room
  - friends : اتاق خصوصی با یک کدِ ۵ کاراکتریِ قابل اشتراک‌گذاری
  - system  : اتاق فوری که همه‌ی صندلی‌های خالی‌اش از ابتدا با هوش مصنوعی
              (bot_controlled=True) پر می‌شود — همان مکانیزمِ از قبل
              تست‌شده‌ی جایگزینیِ بازیکنِ قطع‌شده، فقط اینجا از ابتدا فعال است.
"""

import asyncio
import random
import string
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List

from fastapi import WebSocket

from game_logic import GameEngineFactory, GAME_CATALOG, BaseGame
import ai_opponent

CATALOG_BY_ID = {g["id"]: g for g in GAME_CATALOG}

# شناسه‌ی صندلی‌های هوش مصنوعی در حالت «بازی با سیستم» — این‌ها هیچ‌وقت
# به یک کاربر واقعی/توکن نشست وصل نیستند، فقط مثل یک user_id عادی داخل
# موتور بازی رفتار می‌شوند و از همان لحظه‌ی ساخت bot_controlled=True هستند.
AI_SEAT_PREFIX = "ai_bot_"
AI_SEAT_NAMES = ["ربات هوشمند", "حریف سیستم", "دستیار بازی"]

ROOM_CODE_CHARS = string.ascii_uppercase + string.digits


def _generate_room_code() -> str:
    return "".join(random.choices(ROOM_CODE_CHARS, k=5))


@dataclass
class Room:
    room_id: str
    game_type: str
    engine: BaseGame
    connections: Dict[str, WebSocket] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    bet_charged: bool = False
    payout_done: bool = False
    xp_awarded: bool = False
    mode: str = "random"             # "random" | "friends" | "system"
    room_code: Optional[str] = None  # فقط برای mode == "friends"

    @property
    def max_players(self) -> int:
        return self.engine.config.get("max_players", 4)

    @property
    def is_full(self) -> bool:
        return len(self.engine.players) >= self.max_players


class GameConnectionManager:
    def __init__(self):
        self.rooms: Dict[str, Room] = {}
        # صف اتاق‌های نیمه‌پر به تفکیک نوع بازی، فقط برای حالت random
        self._waiting: Dict[str, List[str]] = {}
        # کد اتاق -> room_id، فقط برای حالت friends
        self._codes: Dict[str, str] = {}
        # تنظیمات هوش مصنوعی که main.py هنگام تغییر در پنل ادمین به‌روز می‌کند
        self.ai_mode: str = "rule_based"          # "rule_based" | "api"
        self.ai_endpoints: List[Dict[str, str]] = []

    # ------------------------------------------------------------
    # ساخت اتاق پایه (مشترک بین هر سه حالت)
    # ------------------------------------------------------------
    def _new_engine(self, game_type: str) -> Room:
        catalog_entry = CATALOG_BY_ID.get(game_type, {})
        config = {
            "min_players": catalog_entry.get("min_players", 2),
            "max_players": catalog_entry.get("max_players", 4),
        }
        room_id = f"{game_type}_{uuid.uuid4().hex[:8]}"
        engine = GameEngineFactory.create_game(game_type, room_id, config)
        return Room(room_id=room_id, game_type=game_type, engine=engine)

    # ------------------------------------------------------------
    # حالت ۱: random — matchmaking عمومی (رفتار قبلی، بدون تغییر)
    # ------------------------------------------------------------
    def _create_room(self, game_type: str) -> Room:
        room = self._new_engine(game_type)
        room.mode = "random"
        self.rooms[room.room_id] = room
        self._waiting.setdefault(game_type, []).append(room.room_id)
        return room

    def find_or_create_room(self, game_type: str) -> Room:
        queue = self._waiting.get(game_type, [])
        while queue:
            room_id = queue[0]
            room = self.rooms.get(room_id)
            if room is None or room.engine.is_started or room.is_full:
                queue.pop(0)
                continue
            return room
        return self._create_room(game_type)

    # ------------------------------------------------------------
    # حالت ۲: friends — اتاق خصوصی با کد اشتراک‌گذاری
    # ------------------------------------------------------------
    def create_friends_room(self, game_type: str) -> Room:
        room = self._new_engine(game_type)
        room.mode = "friends"
        code = _generate_room_code()
        while code in self._codes:
            code = _generate_room_code()
        room.room_code = code
        self.rooms[room.room_id] = room
        self._codes[code] = room.room_id
        return room

    def find_friends_room_by_code(self, code: str) -> Optional[Room]:
        room_id = self._codes.get((code or "").strip().upper())
        if not room_id:
            return None
        room = self.rooms.get(room_id)
        if room is None or room.engine.is_started or room.is_full:
            return None
        return room

    # ------------------------------------------------------------
    # حالت ۳: system — اتاق فوری با صندلی‌های هوش مصنوعی
    # ------------------------------------------------------------
    def create_system_room(self, game_type: str, user_id: str, username: str) -> Room:
        room = self._new_engine(game_type)
        room.mode = "system"
        max_players = room.max_players
        room.engine.add_player(user_id, username)
        room.engine.bot_controlled[user_id] = False

        for i in range(max_players - 1):
            ai_id = f"{AI_SEAT_PREFIX}{uuid.uuid4().hex[:6]}"
            ai_name = AI_SEAT_NAMES[i % len(AI_SEAT_NAMES)]
            room.engine.add_player(ai_id, ai_name, avatar="🤖")
            room.engine.bot_controlled[ai_id] = True

        room.engine.start_game()
        self.rooms[room.room_id] = room
        return room

    # ------------------------------------------------------------
    # اتصال / قطع اتصال
    # ------------------------------------------------------------
    async def join(self, game_type: str, user_id: str, username: str, websocket: WebSocket, room: Optional[Room] = None):
        """
        بازیکن را به یک اتاق وصل می‌کند. اگر room از قبل مشخص نشده باشد
        (حالت random قدیمی)، از matchmaking عمومی استفاده می‌شود.
        خروجی: (room, just_started).
        """
        if room is None:
            room = self.find_or_create_room(game_type)
        async with room.lock:
            already_joined = user_id in room.engine.player_info
            if not already_joined:
                added = room.engine.add_player(user_id, username)
                if not added:
                    return None, False

            await websocket.accept()
            room.connections[user_id] = websocket
            if user_id in room.engine.player_info:
                room.engine.player_info[user_id]["is_connected"] = True
            if hasattr(room.engine, "bot_controlled"):
                room.engine.bot_controlled[user_id] = False

            just_started = False
            if not room.engine.is_started and room.is_full:
                if room.engine.start_game():
                    just_started = True
                    if room.mode == "random" and room.room_id in self._waiting.get(game_type, []):
                        self._waiting[game_type].remove(room.room_id)

        return room, just_started

    def disconnect(self, room: Room, user_id: str):
        if user_id in room.connections:
            del room.connections[user_id]
        if user_id in room.engine.player_info:
            room.engine.player_info[user_id]["is_connected"] = False
        if not room.connections and not room.engine.is_started:
            self.rooms.pop(room.room_id, None)
            queue = self._waiting.get(room.game_type, [])
            if room.room_id in queue:
                queue.remove(room.room_id)
            if room.room_code:
                self._codes.pop(room.room_code, None)
        elif room.engine.is_started and not room.engine.is_finished:
            if hasattr(room.engine, "bot_controlled"):
                room.engine.bot_controlled[user_id] = True

    # ------------------------------------------------------------
    # وقتی نوبتِ بازیکنِ bot_controlled برسد، به‌جای او یک حرکتِ قانونی
    # انجام می‌دهد — یا با منطق ساده‌ی قانون‌محور (rule_based) یا با
    # یک سرویس هوش مصنوعیِ خارجی (api، با fallback چندگانه)، بسته به
    # ai_mode که مالک از پنل مدیریت تنظیم کرده. این کار را پشت‌سرهم تکرار
    # می‌کند تا نوبت به یک بازیکنِ واقعیِ متصل برسد یا بازی تمام شود.
    # ------------------------------------------------------------
    async def _choose_action_for(self, room: Room, current: str):
        engine = room.engine
        if self.ai_mode == "api" and self.ai_endpoints:
            legal = []
            try:
                legal = engine.get_legal_actions(current)
            except Exception:
                legal = []
            if legal:
                # فقط خلاصه‌ای امن (دست خودِ حریف + میز/برد عمومی) به سرویس
                # خارجی می‌فرستیم، نه کل player_info بقیه.
                state_summary: Dict[str, Any] = {}
                try:
                    full_view = engine.get_player_view(current)
                    state_summary = {
                        "current_turn": full_view.get("current_turn"),
                        "my_hand": full_view.get("my_hand"),
                        "table_cards": full_view.get("table_cards"),
                        "board": full_view.get("board"),
                    }
                except Exception:
                    state_summary = {}
                try:
                    action = await ai_opponent.choose_action_via_api(
                        self.ai_endpoints, room.game_type, legal, state_summary
                    )
                    if action is not None:
                        return action
                except Exception:
                    pass
        # rule_based یا fallback نهایی وقتی API شکست خورد/غیرفعال بود
        return engine.get_bot_action(current)

    async def run_bot_turns(self, room: Room, max_moves: int = 200) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        bot_controlled = getattr(room.engine, "bot_controlled", {})
        for _ in range(max_moves):
            if not room.engine.is_started or room.engine.is_finished:
                break
            current = room.engine.get_current_turn_player()
            if current is None or not bot_controlled.get(current):
                break
            action = await self._choose_action_for(room, current)
            async with room.lock:
                if action is None:
                    room.engine.advance_turn()
                    continue
                move_action, payload = action
                result = room.engine.handle_action(current, move_action, payload)
            results.append(result)
            if result.get("status") == "error":
                async with room.lock:
                    room.engine.advance_turn()
        return results

    # ------------------------------------------------------------
    # اکشن‌های بازی — همیشه از موتور سمت سرور عبور می‌کنند
    # ------------------------------------------------------------
    async def handle_action(self, room: Room, user_id: str, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with room.lock:
            result = room.engine.handle_action(user_id, action, payload or {})
        await self.broadcast_state(room)
        return result

    # ------------------------------------------------------------
    # ارسال وضعیت شخصی‌سازی‌شده به هر بازیکن (دست هرکس فقط برای خودش)
    # ------------------------------------------------------------
    async def broadcast_state(self, room: Room):
        for user_id, connection in list(room.connections.items()):
            try:
                view = room.engine.get_player_view(user_id)
                await connection.send_json({"type": "STATE_UPDATE", "state": view})
            except Exception:
                pass

    async def send_personal(self, room: Room, user_id: str, message: dict):
        connection = room.connections.get(user_id)
        if connection:
            try:
                await connection.send_json(message)
            except Exception:
                pass

    async def broadcast_raw(self, room: Room, message: dict):
        """برای پیام‌های سبک و بدون تاثیر بر قوانین بازی (ایموجی زنده، تاس تزئینی)."""
        for user_id, connection in list(room.connections.items()):
            try:
                await connection.send_json(message)
            except Exception:
                pass

    # ------------------------------------------------------------
    # آمار برای پنل مدیریت
    # ------------------------------------------------------------
    @property
    def active_rooms_count(self) -> int:
        return len(self.rooms)

    @property
    def online_players_count(self) -> int:
        return sum(len(r.connections) for r in self.rooms.values())


ws_manager = GameConnectionManager()
