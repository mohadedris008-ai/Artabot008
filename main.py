import os
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# اتصال پوشه static (برای جاوااسکریپت و استایل‌ها)
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# ۱. اندپوینت Health Check برای UptimeRobot و بیدار نگه داشتن سرور
@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# ۲. ذخیره اتصالات فعال وب‌سوکت
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, room_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)

    def disconnect(self, room_id: str, websocket: WebSocket):
        if room_id in self.active_connections:
            self.active_connections[room_id].remove(websocket)

    async def broadcast(self, room_id: str, message: dict):
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                await connection.send_json(message)

manager = ConnectionManager()

# ۳. اندپوینت وب‌سوکت برای بازی
@app.websocket("/ws/game/{room_id}/{user_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await manager.connect(room_id, websocket)
    try:
        while True:
            data_text = await websocket.receive_text()
            data = json.loads(data_text)
            
            response = {
                "status": "ok",
                "player": user_id,
                "action": data.get("action"),
                "position": data.get("position"),
                "next_turn": "player_next"
            }
            await manager.broadcast(room_id, response)
            
    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)

# ۴. سرو کردن فایل index.html (بررسی در هر دو مسیر ریشه و پوشه static)
@app.get("/", response_class=HTMLResponse)
async def get_game_page():
    if os.path.exists("static/index.html"):
        with open("static/index.html", "r", encoding="utf-8") as f:
            return f.read()
    elif os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>فایل index.html پیدا نشد!</h1>"

# ۵. دریافت آپدیت‌های تلگرام (Webhook)
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    return {"status": "ok"}
