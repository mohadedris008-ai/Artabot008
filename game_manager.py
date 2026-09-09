"""
مدیریت اتاق‌های بازی (Room Manager)
---------------------------------
نسخه‌ی قبلی این فایل فقط پیام‌های خام کلاینت را broadcast می‌کرد و هیچ
اعتبارسنجی سمت سروری روی بازی انجام نمی‌شد (یعنی موتورهای game_logic.py
مثل PasurGame/HokmGame/SequenceGame هیچ‌وقت واقعاً اجرا نمی‌شدند).

این نسخه هر اتاق را به یک نمونه‌ی واقعی از موتور بازی (GameEngineFactory)
وصل می‌کند؛ همه‌ی اکشن‌های بازیکن از مسیر engine.handle_action() عبور
می‌کنند تا قوانین بازی همیشه سمت سرور enforce بشه، نه سمت کلاینت.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List

from fastapi import WebSocket

from game_logic import GameEngineFactory, GAME_CATALOG, BaseGame

CATALOG_BY_ID = {g["id"]: g for g in GAME_CATALOG}


@dataclass
class Room:
    room_id: str
    game_type: str
    engine: BaseGame
    connections: Dict[str, WebSocket] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    bet_charged: bool = False
    payout_done: bool = False

    @property
    def max_players(self) -> int:
        return self.engine.config.get("max_players", 4)

    @property
    def is_full(self) -> bool:
        return len(self.engine.players) >= self.max_players


class GameConnectionManager:
    def __init__(self):
        self.rooms: Dict[str, Room] = {}
        # صف اتاق‌های نیمه‌پر به تفکیک نوع بازی، برای matchmaking ساده
        self._waiting: Dict[str, List[str]] = {}

    # ------------------------------------------------------------
    # پیدا کردن یا ساختن اتاق (matchmaking ساده)
    # ------------------------------------------------------------
    def _create_room(self, game_type: str) -> Room:
        catalog_entry = CATALOG_BY_ID.get(game_type, {})
        config = {
            "min_players": catalog_entry.get("min_players", 2),
            "max_players": catalog_entry.get("max_players", 4),
        }
        room_id = f"{game_type}_{uuid.uuid4().hex[:8]}"
        engine = GameEngineFactory.create_game(game_type, room_id, config)
        room = Room(room_id=room_id, game_type=game_type, engine=engine)
        self.rooms[room_id] = room
        self._waiting.setdefault(game_type, []).append(room_id)
        return room

    def find_or_create_room(self, game_type: str) -> Room:
        queue = self._waiting.get(game_type, [])
        # اولین اتاق منتظر (هنوز شروع نشده و جا دارد) را پیدا کن
        while queue:
            room_id = queue[0]
            room = self.rooms.get(room_id)
            if room is None or room.engine.is_started or room.is_full:
                queue.pop(0)
                continue
            return room
        return self._create_room(game_type)

    # ------------------------------------------------------------
    # اتصال / قطع اتصال
    # ------------------------------------------------------------
    async def join(self, game_type: str, user_id: str, username: str, websocket: WebSocket):
        """
        بازیکن را به یک اتاق مناسب (موجود یا جدید) وصل می‌کند.
        خروجی: (room, just_started) که just_started یعنی همین الان با
        وصل شدن این بازیکن، اتاق پر شد و بازی شروع شد (برای کسر شرط‌بندی
        در main.py استفاده می‌شود).
        """
        room = self.find_or_create_room(game_type)
        async with room.lock:
            already_joined = user_id in room.engine.player_info
            if not already_joined:
                added = room.engine.add_player(user_id, username)
                if not added:
                    return None, False

            await websocket.accept()
            room.connections[user_id] = websocket

            just_started = False
            if not room.engine.is_started and room.is_full:
                if room.engine.start_game():
                    just_started = True
                    if room.room_id in self._waiting.get(game_type, []):
                        self._waiting[game_type].remove(room.room_id)

        return room, just_started

    def disconnect(self, room: Room, user_id: str):
        if user_id in room.connections:
            del room.connections[user_id]
        if user_id in room.engine.player_info:
            room.engine.player_info[user_id]["is_connected"] = False
        # اتاقی که هیچ اتصال زنده‌ای ندارد و بازی‌اش هم شروع نشده را پاک می‌کنیم
        if not room.connections and not room.engine.is_started:
            self.rooms.pop(room.room_id, None)
            queue = self._waiting.get(room.game_type, [])
            if room.room_id in queue:
                queue.remove(room.room_id)

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
