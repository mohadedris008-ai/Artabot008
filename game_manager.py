import asyncio
from typing import Dict, Optional
from fastapi import WebSocket


class GameConnectionManager:
    def __init__(self):
        self.active_rooms: Dict[str, Dict[str, WebSocket]] = {}
        self.room_states: Dict[str, dict] = {}
        self._room_locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, room_id: str) -> asyncio.Lock:
        if room_id not in self._room_locks:
            self._room_locks[room_id] = asyncio.Lock()
        return self._room_locks[room_id]

    async def connect(self, room_id: str, user_id: str, websocket: WebSocket):
        await websocket.accept()
        lock = self._get_lock(room_id)

        async with lock:
            if room_id not in self.active_rooms:
                self.active_rooms[room_id] = {}
                self.room_states[room_id] = {
                    "room_id": room_id,
                    "players": {},       # user_id -> {hand: [...], score: 0, ...}
                    "table_cards": [],
                    "status": "WAITING",
                    "current_turn": None,
                }

            self.active_rooms[room_id][user_id] = websocket

            state = self.room_states[room_id]
            if user_id not in state["players"]:
                state["players"][user_id] = {"hand": [], "score": 0, "connected": True}
            else:
                # کاربر دوباره وصل شده — وضعیت قبلی (دست کارت‌ها و ...) حفظ می‌شود
                state["players"][user_id]["connected"] = True

        await self.broadcast(room_id, {
            "type": "PLAYER_JOINED",
            "user_id": user_id,
            "total_online": len(self.active_rooms[room_id]),
            "room_state": self.room_states[room_id],
        })

    def disconnect(self, room_id: str, user_id: str):
        if room_id in self.active_rooms and user_id in self.active_rooms[room_id]:
            del self.active_rooms[room_id][user_id]

            # وضعیت بازیکن (دست کارت‌ها و امتیاز) حفظ می‌شود تا در صورت
            # وصل شدن مجدد از دست نرود؛ فقط پرچم connected خاموش می‌شود.
            state = self.room_states.get(room_id)
            if state and user_id in state["players"]:
                state["players"][user_id]["connected"] = False

            if not self.active_rooms[room_id]:
                del self.active_rooms[room_id]
                # اتاق را کامل پاک نمی‌کنیم اگر هنوز بازیکنانی با connected=False
                # در حال بازی هستند و ممکن است برگردند؛ اینجا برای سادگی پاک می‌کنیم.
                if room_id in self.room_states:
                    del self.room_states[room_id]
                if room_id in self._room_locks:
                    del self._room_locks[room_id]

    async def send_personal(self, websocket: WebSocket, message: dict):
        await websocket.send_json(message)

    async def broadcast(self, room_id: str, message: dict):
        if room_id in self.active_rooms:
            for user_id, connection in list(self.active_rooms[room_id].items()):
                try:
                    await connection.send_json(message)
                except Exception:
                    pass

    def get_state(self, room_id: str) -> Optional[dict]:
        return self.room_states.get(room_id)


ws_manager = GameConnectionManager()
