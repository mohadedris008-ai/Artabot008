import os
import json
import time
import random
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Header
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, init_db, User, Transaction
from telegram_auth import verify_telegram_init_data, create_session_token, verify_session_token
from game_manager import ws_manager

app = FastAPI(title="Telegram Game Hub Platform")

# قفل‌های per-user برای جلوگیری از race condition روی سکه (تک-پردازه‌ای؛
# برای چند-instance باید با یک قفل توزیع‌شده مثل Redis جایگزین شود)
_user_locks: dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


@app.on_event("startup")
async def on_startup():
    init_db()


if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}


@app.get("/")
async def get_index():
    possible_paths = ["static/index.html", "index.html", "public/index.html"]
    for path in possible_paths:
        if os.path.exists(path):
            return FileResponse(path)
    return HTMLResponse("<h1>سرور فعال است! فایل index.html یافت نشد.</h1>", status_code=200)


# ---------------------------------------------------------
# احراز هویت مشترک با توکن نشست (Authorization: Bearer <token>)
# ---------------------------------------------------------
def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="توکن ارسال نشده است")

    token = authorization.removeprefix("Bearer ").strip()
    user_id = verify_session_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="توکن نامعتبر یا منقضی شده")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="کاربر یافت نشد")
    if user.is_banned:
        raise HTTPException(status_code=403, detail="حساب شما مسدود شده است")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز")
    return user


# ---------------------------------------------------------
# وب‌سوکت اصلی بازی‌ها
# ---------------------------------------------------------
@app.websocket("/ws/game/{room_id}/{user_id}")
async def game_websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await ws_manager.connect(room_id, user_id, websocket)
    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            action = data.get("action")
            state = ws_manager.get_state(room_id)
            if state is None:
                continue

            if action == "PLAY_CARD":
                card = data.get("card")
                state["table_cards"].append({"played_by": user_id, "card": card})
                await ws_manager.broadcast(room_id, {
                    "type": "CARD_PLAYED",
                    "user_id": user_id,
                    "card": card,
                    "table_cards": state["table_cards"],
                })

            elif action == "SEND_EMOJI":
                emoji = data.get("emoji")
                await ws_manager.broadcast(room_id, {
                    "type": "LIVE_EMOJI",
                    "from_user": user_id,
                    "emoji": emoji,
                })

            elif action == "ROLL_DICE":
                dice_val = random.randint(1, 6)
                await ws_manager.broadcast(room_id, {
                    "type": "DICE_ROLLED",
                    "user_id": user_id,
                    "value": dice_val,
                })

    except WebSocketDisconnect:
        ws_manager.disconnect(room_id, user_id)
        await ws_manager.broadcast(room_id, {"type": "PLAYER_LEFT", "user_id": user_id})


# ---------------------------------------------------------
# REST API - ورود و همگام‌سازی کاربر
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
            referred_by=payload.referrer_id if payload.referrer_id != user_id else None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        if user.referred_by:
            ref_user = db.query(User).filter(User.id == user.referred_by).first()
            if ref_user:
                ref_user.coins += 500
                db.add(Transaction(user_id=ref_user.id, amount=500, type="REFERRAL"))
                db.commit()

    if user.is_banned:
        raise HTTPException(status_code=403, detail="Your account is banned.")

    token = create_session_token(user.id)

    return {
        "token": token,
        "id": user.id,
        "first_name": user.first_name,
        "coins": user.coins,
        "gems": user.gems,
        "is_admin": user.is_admin,
        "card_back_skin": user.card_back_skin,
        "avatar_skin": user.avatar_skin,
    }


# ---------------------------------------------------------
# REST API - اقتصاد بازی (با قفل per-user برای جلوگیری از race condition)
# ---------------------------------------------------------
@app.post("/api/economy/daily-reward")
async def claim_daily_reward(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    async with _get_user_lock(user.id):
        db.refresh(user)
        now = datetime.utcnow()

        if user.last_daily_claim and (now - user.last_daily_claim) < timedelta(hours=24):
            time_left = timedelta(hours=24) - (now - user.last_daily_claim)
            return JSONResponse(
                status_code=400,
                content={"message": f"تا دریافت بعدی {int(time_left.total_seconds() // 3600)} ساعت مانده است."},
            )

        reward_coins = 200
        user.coins += reward_coins
        user.last_daily_claim = now
        db.add(Transaction(user_id=user.id, amount=reward_coins, type="DAILY_REWARD"))
        db.commit()

        return {"status": "success", "new_coins": user.coins, "reward": reward_coins}


@app.post("/api/economy/spin-wheel")
async def spin_wheel(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    async with _get_user_lock(user.id):
        db.refresh(user)
        cost = 100
        if user.coins < cost:
            raise HTTPException(status_code=400, detail="سکه کافی ندارید!")

        user.coins -= cost
        prizes = [50, 100, 250, 500, 1000, "skin_gold"]
        won = random.choice(prizes)

        if isinstance(won, int):
            user.coins += won
            msg = f"شما {won} سکه برنده شدید!"
            net_amount = won - cost
        else:
            user.card_back_skin = "gold"
            msg = "پوسته کارت طلایی اختصاصی را باز کردید!"
            net_amount = -cost

        db.add(Transaction(user_id=user.id, amount=net_amount, type="WHEEL"))
        db.commit()

        return {
            "prize": won,
            "message": msg,
            "new_coins": user.coins,
            "card_back_skin": user.card_back_skin,
        }


# ---------------------------------------------------------
# پنل مدیریت ارشد (فقط با توکن معتبرِ کاربر ادمین)
# ---------------------------------------------------------
@app.get("/api/admin/stats")
async def get_admin_stats(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    total_users = db.query(User).count()
    active_rooms_count = len(ws_manager.active_rooms)
    sum_coins = sum(c[0] for c in db.query(User).with_entities(User.coins).all())

    return {
        "total_users": total_users,
        "active_rooms": active_rooms_count,
        "total_coins": sum_coins,
        "online_players_count": sum(len(room) for room in ws_manager.active_rooms.values()),
    }


class BanPayload(BaseModel):
    target_user_id: int


@app.post("/api/admin/ban")
async def ban_user(payload: BanPayload, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == payload.target_user_id).first()
    if target:
        target.is_banned = True
        db.commit()
        return {"status": "ok", "message": f"کاربر {payload.target_user_id} مسدود شد."}
    raise HTTPException(status_code=404, detail="کاربر یافت نشد")
