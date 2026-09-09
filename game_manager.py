from typing import Dict
from fastapi import WebSocket

class GameConnectionManager:
    def __init__(self):
        self.active_rooms: Dict[str, Dict[str, WebSocket]] = {}
        self.room_states: Dict[str, dict] = {}

    async def connect(self, room_id: str, user_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_id not in self.active_rooms:
            self.active_rooms[room_id] = {}
            self.room_states[room_id] = {
                "room_id": room_id,
                "players": {},
                "table_cards": [],
                "status": "WAITING",
                "current_turn": None
            }
        
        self.active_rooms[room_id][user_id] = websocket
        
        await self.broadcast(room_id, {
            "type": "PLAYER_JOINED",
            "user_id": user_id,
            "total_online": len(self.active_rooms[room_id]),
            "room_state": self.room_states[room_id]
        })

    def disconnect(self, room_id: str, user_id: str):
        if room_id in self.active_rooms and user_id in self.active_rooms[room_id]:
            del self.active_rooms[room_id][user_id]
            if not self.active_rooms[room_id]:
                del self.active_rooms[room_id]
                if room_id in self.room_states:
                    del self.room_states[room_id]

    async def send_personal(self, websocket: WebSocket, message: dict):
        await websocket.send_json(message)

    async def broadcast(self, room_id: str, message: dict):
        if room_id in self.active_rooms:
            for user_id, connection in list(self.active_rooms[room_id].items()):
                try:
                    await connection.send_json(message)
                except Exception:
                    pass

ws_manager = GameConnectionManager()
