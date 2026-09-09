import os
import json
import time
import random
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Header, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, init_db, User, Transaction, SessionLocal
from telegram_auth import verify_telegram_init_data, create_session_token, verify_session_token, SESSION_SECRET
from game_manager import ws_manager
from game_logic import GAME_CATALOG

app = FastAPI(title="Telegram Game Hub Platform")

if SESSION_SECRET == "change-me-in-production":
    print(
        "\n"
        "!!! هشدار امنیتی: متغیر محیطی SESSION_SECRET تنظیم نشده و از مقدار\n"
        "!!! پیش‌فرض ناامن استفاده می‌شود. در محیط production حتماً یک مقدار\n"
        "!!! تصادفی و طولانی برای SESSION_SECRET ست کنید، وگرنه توکن‌های\n"
        "!!! نشست کاربران قابل جعل هستند.\n"
    )

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
# اقتصاد بازی: کسر شرط هنگام شروع و پرداخت جایزه هنگام پایان
# (این بخش قبلاً هیچ‌جا صدا زده نمی‌شد و min_bet در GAME_CATALOG
#  عملاً بلااستفاده بود.)
# ---------------------------------------------------------
def _charge_bets(db: Session, room) -> Optional[str]:
    """شرط هر بازیکن را کسر می‌کند. اگر کسی سکه‌ی کافی نداشت، بدون کسر از
    کسی، پیام خطا برمی‌گرداند (بازی این دور شروع نمی‌شود ولی اتاق باز می‌ماند)."""
    catalog_entry = next((g for g in GAME_CATALOG if g["id"] == room.game_type), None)
    min_bet = catalog_entry.get("min_bet", 0) if catalog_entry else 0
    if min_bet <= 0:
        room.bet_charged = True
        return None

    user_ids = [int(uid) for uid in room.engine.players]
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    users_by_id = {u.id: u for u in users}

    for uid in user_ids:
        u = users_by_id.get(uid)
        if not u or u.coins < min_bet:
            return f"سکه‌ی کافی برای شرط‌بندی این بازی ({min_bet}) وجود ندارد."

    for uid in user_ids:
        u = users_by_id[uid]
        u.coins -= min_bet
        db.add(Transaction(user_id=uid, amount=-min_bet, type="GAME_BET"))
    db.commit()
    room.bet_charged = True
    return None


def _payout_winner(db: Session, room):
    if room.payout_done:
        return
    room.payout_done = True

    catalog_entry = next((g for g in GAME_CATALOG if g["id"] == room.game_type), None)
    min_bet = catalog_entry.get("min_bet", 0) if catalog_entry else 0
    winner_id = room.engine.winner
    if not winner_id or not room.bet_charged or min_bet <= 0:
        return

    pot = min_bet * len(room.engine.players)
    try:
        winner_uid = int(winner_id)
    except (TypeError, ValueError):
        return

    winner = db.query(User).filter(User.id == winner_uid).first()
    if winner:
        winner.coins += pot
        db.add(Transaction(user_id=winner_uid, amount=pot, type="GAME_WIN"))
        db.commit()


# ---------------------------------------------------------
# وب‌سوکت اصلی بازی‌ها
# مسیر بر اساس نوع بازی است (نه یک room_id ثابت که کلاینت انتخاب کند)؛
# سرور خودش با matchmaking ساده یک اتاق مناسب پیدا/می‌سازد. احراز هویت
# با توکن نشست (همان Bearer token که از /api/user/sync گرفته شده) انجام
# می‌شود تا کسی نتواند جای کاربر دیگری وصل شود.
# ---------------------------------------------------------
@app.websocket("/ws/game/{game_type}/{user_id}")
async def game_websocket_endpoint(
    websocket: WebSocket,
    game_type: str,
    user_id: str,
    token: str = Query(...),
):
    verified_user_id = verify_session_token(token)
    if verified_user_id is None or str(verified_user_id) != str(user_id):
        await websocket.close(code=4401)
        return

    valid_game_ids = {g["id"] for g in GAME_CATALOG if g.get("active")}
    if game_type not in valid_game_ids:
        await websocket.close(code=4404)
        return

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == verified_user_id).first()
        if not user or user.is_banned:
            await websocket.close(code=4403)
            return
        username = user.first_name
    finally:
        db.close()

    room, just_started = await ws_manager.join(game_type, user_id, username, websocket)
    if room is None:
        await websocket.close(code=4409)  # اتاق پر است
        return

    if just_started:
        db = SessionLocal()
        try:
            error = _charge_bets(db, room)
        finally:
            db.close()
        if error:
            await ws_manager.broadcast_raw(room, {"type": "ERROR", "message": error})

    await ws_manager.broadcast_state(room)

    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                data = json.loads(raw_data)
            except (ValueError, TypeError):
                continue
            action = data.get("action")
            payload = data.get("payload") or {}

            if action == "send_emoji":
                await ws_manager.broadcast_raw(room, {
                    "type": "LIVE_EMOJI",
                    "from_user": user_id,
                    "emoji": str(payload.get("emoji", "🔥"))[:8],
                })
                continue

            if action == "roll_dice":
                dice_val = random.randint(1, 6)
                await ws_manager.broadcast_raw(room, {
                    "type": "DICE_ROLLED",
                    "user_id": user_id,
                    "value": dice_val,
                })
                continue

            # هر اکشن دیگری (play_card / declare_trump / place_chip / ...)
            # مستقیم به موتور سمت سرور بازی همان room ارسال می‌شود.
            result = await ws_manager.handle_action(room, user_id, action, payload)
            if result and result.get("status") == "error":
                await ws_manager.send_personal(room, user_id, {"type": "ACTION_ERROR", "message": result["message"]})
            if result and result.get("is_finished"):
                db = SessionLocal()
                try:
                    _payout_winner(db, room)
                finally:
                    db.close()
                await ws_manager.broadcast_raw(room, {"type": "GAME_OVER", "winner": room.engine.winner})

    except WebSocketDisconnect:
        ws_manager.disconnect(room, user_id)
        await ws_manager.broadcast_raw(room, {"type": "PLAYER_LEFT", "user_id": user_id})


# ---------------------------------------------------------
# فهرست بازی‌های فعال (تا فرانت‌اند لیست هاب را هاردکد نکند)
# ---------------------------------------------------------
@app.get("/api/games/catalog")
async def get_games_catalog():
    return {"games": [g for g in GAME_CATALOG if g.get("active")]}


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


@app.get("/api/user/me")
async def get_me(user: User = Depends(get_current_user)):
    return {
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
    sum_coins = sum(c[0] for c in db.query(User).with_entities(User.coins).all())

    return {
        "total_users": total_users,
        "active_rooms": ws_manager.active_rooms_count,
        "total_coins": sum_coins,
        "online_players_count": ws_manager.online_players_count,
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
