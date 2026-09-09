import os
import json
import time
import hmac
import hashlib
import urllib.parse
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, HTTPException, status, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, BigInteger, Boolean, DateTime, Float, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

# ---------------------------------------------------------
# ۱. تنظیمات دیتابیس و اعتبارسنجی
# ---------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "7900000000:AAFAKE_TOKEN_FOR_DEV_PURPOSES_ONLY")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./game_hub.db")

# تبدیل postgres:// به postgresql:// در صورت دیپلوی روی پلتفرم‌هایی مانند Render/Heroku
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------------------------------------------------------
# ۲. مدل‌های دیتابیس (SQLAlchemy Models)
# ---------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True) # Telegram User ID
    first_name = Column(String, default="Gamer")
    username = Column(String, nullable=True)
    coins = Column(Integer, default=1000)
    gems = Column(Integer, default=10)
    avatar_skin = Column(String, default="default")
    card_back_skin = Column(String, default="classic")
    is_admin = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    referred_by = Column(BigInteger, nullable=True)
    last_daily_claim = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"))
    amount = Column(Integer)
    type = Column(String)  # 'DAILY_REWARD', 'GAME_WIN', 'GAME_FEE', 'REFERRAL', 'WHEEL'
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------------------------------------------------------
# ۳. سیستم اعتبارسنجی Telegram initData
# ---------------------------------------------------------
def verify_telegram_init_data(init_data: str) -> Optional[dict]:
    if not init_data or BOT_TOKEN.startswith("7900000000"):
        # حالت توسعه (Development Mode)
        return {"id": 12345678, "first_name": "بازیکن تست", "username": "test_user"}
    
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        if "hash" not in parsed_data:
            return None
        
        hash_check = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash == hash_check:
            user_data = json.loads(parsed_data.get("user", "{}"))
            return user_data
    except Exception as e:
        print(f"Auth error: {e}")
    return None

# ---------------------------------------------------------
# ۴. مدیریت ارتباطات زنده WebSocket (Connection Manager)
# ---------------------------------------------------------
class GameConnectionManager:
    def __init__(self):
        # room_id -> { user_id: WebSocket }
        self.active_rooms: Dict[str, Dict[str, WebSocket]] = {}
        # room_id -> state dict
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
        
        # اطلاع‌رسانی ورود مجدد یا جدید کاربر به اتاق
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

# ---------------------------------------------------------
# ۵. اپلیکیشن اصلی FastAPI
# ---------------------------------------------------------
app = FastAPI(title="Telegram Game Hub Platform")

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}

@app.get("/", response_class=HTMLResponse)
async def get_index():
    path = "static/index.html" if os.path.exists("static/index.html") else "index.html"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>فایل index.html یافت نشد. لطفاً ساختار پوشه‌ها را بررسی کنید.</h1>"

# ---------------------------------------------------------
# ۶. وب‌سوکت اصلی بازی‌ها (WebSocket Route)
# ---------------------------------------------------------
@app.websocket("/ws/game/{room_id}/{user_id}")
async def game_websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await ws_manager.connect(room_id, user_id, websocket)
    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            action = data.get("action")

            state = ws_manager.room_states.get(room_id, {})

            if action == "PLAY_CARD":
                card = data.get("card")
                if "table_cards" not in state:
                    state["table_cards"] = []
                state["table_cards"].append({"played_by": user_id, "card": card})
                
                await ws_manager.broadcast(room_id, {
                    "type": "CARD_PLAYED",
                    "user_id": user_id,
                    "card": card,
                    "table_cards": state["table_cards"]
                })

            elif action == "SEND_EMOJI":
                emoji = data.get("emoji")
                await ws_manager.broadcast(room_id, {
                    "type": "LIVE_EMOJI",
                    "from_user": user_id,
                    "emoji": emoji
                })

            elif action == "ROLL_DICE":
                import random
                dice_val = random.randint(1, 6)
                await ws_manager.broadcast(room_id, {
                    "type": "DICE_ROLLED",
                    "user_id": user_id,
                    "value": dice_val
                })

    except WebSocketDisconnect:
        ws_manager.disconnect(room_id, user_id)
        await ws_manager.broadcast(room_id, {
            "type": "PLAYER_LEFT",
            "user_id": user_id
        })

# ---------------------------------------------------------
# ۷. اندپوینت‌های REST API برای اقتصاد و کاربران
# ---------------------------------------------------------
class AuthPayload(BaseModel):
    initData: str
    referrer_id: Optional[int] = None

@app.post("/api/user/sync")
async def sync_user(payload: AuthPayload, db: Session = Depends(get_db)):
    tg_user = verify_telegram_init_data(payload.initData)
    if not tg_user:
        raise HTTPException(status_code=401, detail="Invalid Telegram Data")

    user_id = tg_user["id"]
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        user = User(
            id=user_id,
            first_name=tg_user.get("first_name", "Gamer"),
            username=tg_user.get("username"),
            referred_by=payload.referrer_id if payload.referrer_id != user_id else None
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # پاداش دعوت کننده
        if user.referred_by:
            ref_user = db.query(User).filter(User.id == user.referred_by).first()
            if ref_user:
                ref_user.coins += 500
                db.add(Transaction(user_id=ref_user.id, amount=500, type="REFERRAL"))
                db.commit()

    if user.is_banned:
        raise HTTPException(status_code=403, detail="Your account is banned.")

    return {
        "id": user.id,
        "first_name": user.first_name,
        "coins": user.coins,
        "gems": user.gems,
        "is_admin": user.is_admin,
        "card_back_skin": user.card_back_skin,
        "avatar_skin": user.avatar_skin
    }

@app.post("/api/economy/daily-reward")
async def claim_daily_reward(initData: str, db: Session = Depends(get_db)):
    tg_user = verify_telegram_init_data(initData)
    if not tg_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = db.query(User).filter(User.id == tg_user["id"]).first()
    now = datetime.utcnow()

    if user.last_daily_claim and (now - user.last_daily_claim) < timedelta(hours=24):
        time_left = timedelta(hours=24) - (now - user.last_daily_claim)
        return JSONResponse(status_code=400, content={"message": f"تا دریافت بعدی {int(time_left.total_seconds() // 3600)} ساعت مانده است."})

    reward_coins = 200
    user.coins += reward_coins
    user.last_daily_claim = now
    db.add(Transaction(user_id=user.id, amount=reward_coins, type="DAILY_REWARD"))
    db.commit()

    return {"status": "success", "new_coins": user.coins, "reward": reward_coins}

@app.post("/api/economy/spin-wheel")
async def spin_wheel(initData: str, db: Session = Depends(get_db)):
    import random
    tg_user = verify_telegram_init_data(initData)
    if not tg_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = db.query(User).filter(User.id == tg_user["id"]).first()
    cost = 100
    if user.coins < cost:
        raise HTTPException(status_code=400, detail="سکه کافی ندارید!")

    user.coins -= cost
    prizes = [50, 100, 250, 500, 1000, "skin_gold"]
    won = random.choice(prizes)

    if isinstance(won, int):
        user.coins += won
        msg = f"شما {won} سکه برنده شدید!"
    else:
        user.card_back_skin = "gold"
        msg = "پوسته کارت طلایی اختصاصی را باز کردید!"

    db.add(Transaction(user_id=user.id, amount=-cost if isinstance(won, str) else won-cost, type="WHEEL"))
    db.commit()

    return {"prize": won, "message": msg, "new_coins": user.coins, "card_back_skin": user.card_back_skin}

# ---------------------------------------------------------
# ۸. پنل مدیریت ارشد (Admin & Owner Endpoints)
# ---------------------------------------------------------
@app.get("/api/admin/stats")
async def get_admin_stats(admin_id: int = Query(...), db: Session = Depends(get_db)):
    admin = db.query(User).filter(User.id == admin_id, User.is_admin == True).first()
    if not admin:
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز")

    total_users = db.query(User).count()
    active_rooms_count = len(ws_manager.active_rooms)
    total_coins_in_economy = db.query(User).with_entities(User.coins).all()
    sum_coins = sum(c[0] for c in total_coins_in_economy)

    return {
        "total_users": total_users,
        "active_rooms": active_rooms_count,
        "total_coins": sum_coins,
        "online_players_count": sum(len(room) for room in ws_manager.active_rooms.values())
    }

@app.post("/api/admin/ban")
async def ban_user(admin_id: int, target_user_id: int, db: Session = Depends(get_db)):
    admin = db.query(User).filter(User.id == admin_id, User.is_admin == True).first()
    if not admin:
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز")

    target = db.query(User).filter(User.id == target_user_id).first()
    if target:
        target.is_banned = True
        db.commit()
        return {"status": "ok", "message": f"کاربر {target_user_id} مسدود شد."}
    raise HTTPException(status_code=44, detail="کاربر یافت نشد")
