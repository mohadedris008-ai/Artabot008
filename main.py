import os
import json
import time
import random
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

# ایمپورت ماژول‌های جداشده
from database import get_db, User, Transaction
from telegram_auth import verify_telegram_init_data
from game_manager import ws_manager

app = FastAPI(title="Telegram Game Hub Platform")

# ---------------------------------------------------------
# سرو کردن فایل‌های الاستیک و مکان‌یابی هوشمند index.html
# ---------------------------------------------------------
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}

@app.get("/")
async def get_index():
    """روت اصلی - جستجوی هوشمند برای فایل index.html"""
    possible_paths = [
        "static/index.html",
        "index.html",
        "public/index.html"
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return FileResponse(path)
            
    # اگر فایل html موجود نبود، صفحه ساده قرار داده می‌شود تا ارور 404 ندهد
    return HTMLResponse("<h1>سرور فعال است! فایل index.html درون پوشه static یافت نشد.</h1>", status_code=200)

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
# REST API - اقتصاد و کاربران
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
# پنل مدیریت ارشد
# ---------------------------------------------------------
@app.get("/api/admin/stats")
async def get_admin_stats(admin_id: int = Query(...), db: Session = Depends(get_db)):
    admin = db.query(User).filter(User.id == admin_id, User.is_admin == True).first()
    if not admin:
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز")

    total_users = db.query(User).count()
    active_rooms_count = len(ws_manager.active_rooms)
    sum_coins = sum(c[0] for c in db.query(User).with_entities(User.coins).all())

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
    raise HTTPException(status_code=404, detail="کاربر یافت نشد")
