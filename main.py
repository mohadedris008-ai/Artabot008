import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from game_logic import GameRoom

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "123456789"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://your-app.onrender.com/webhook")
DATABASE = "game_platform.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
active_rooms = {}

def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            coins INTEGER DEFAULT 1000,
            role TEXT DEFAULT 'player',
            is_banned INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            category TEXT,
            user_id INTEGER,
            details TEXT
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO users (user_id, role) VALUES (?, 'owner')", (OWNER_ID,))
    conn.commit()
    conn.close()

def log_event(category: str, user_id: int, details: str):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO logs (category, user_id, details) VALUES (?, ?, ?)", 
                   (category, user_id, details))
    conn.commit()
    conn.close()

async def auto_cleanup_logs():
    """Delete logs older than 48 hours automatically"""
    while True:
        try:
            conn = sqlite3.connect(DATABASE)
            cursor = conn.cursor()
            threshold = datetime.utcnow() - timedelta(hours=48)
            cursor.execute("DELETE FROM logs WHERE timestamp < ?", (threshold,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Log cleanup error: {e}")
        await asyncio.sleep(3600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await bot.set_webhook(url=WEBHOOK_URL)
    cleanup_task = asyncio.create_task(auto_cleanup_logs())
    yield
    await bot.delete_webhook()
    cleanup_task.cancel()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

    log_event("USER", user_id, f"User {username} started the bot.")
    base_url = WEBHOOK_URL.rsplit('/', 1)[0]

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 اجرای بازی کارتی (Mini App)", web_app=WebAppInfo(url=f"{base_url}/game"))],
        [InlineKeyboardButton(text="⚙️ پنل مدیریتی", web_app=WebAppInfo(url=f"{base_url}/admin"))]
    ])
    await message.answer(f"سلام {message.from_user.first_name}! به پلتفرم جامع بازی‌های کارتی خوش آمدید.", reply_markup=keyboard)

@app.post("/webhook")
async def telegram_webhook(request: Request):
    update_data = await request.json()
    update = types.Update(**update_data)
    await dp.feed_update(bot, update)
    return {"status": "ok"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow()}

@app.get("/game", response_class=HTMLResponse)
async def serve_game():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/admin/logs")
async def get_logs(admin_id: int):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE user_id = ?", (admin_id,))
    user = cursor.fetchone()
    if not user or user[0] not in ['owner', 'admin']:
        raise HTTPException(status_code=403, detail="Unauthorized access")
        
    cursor.execute("SELECT timestamp, category, user_id, details FROM logs ORDER BY timestamp DESC")
    logs = cursor.fetchall()
    conn.close()
    return [{"time": l[0], "category": l[1], "user_id": l[2], "details": l[3]} for l in logs]

@app.websocket("/ws/game/{room_id}/{user_id}")
async def game_websocket(websocket: WebSocket, room_id: str, user_id: int):
    await websocket.accept()
    if room_id not in active_rooms:
        active_rooms[room_id] = GameRoom(room_id)
    
    room = active_rooms[room_id]
    room.add_player(user_id)
    log_event("GAME", user_id, f"Connected to room {room_id}")

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "play_card":
                res = room.play_card(user_id, data.get("card"), data.get("position"))
                await websocket.send_json(res)
    except WebSocketDisconnect:
        room.remove_player(user_id)
        log_event("GAME", user_id, f"Disconnected from room {room_id}")
