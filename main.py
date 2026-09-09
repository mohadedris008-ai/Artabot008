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
from bot import start_bot_polling, stop_bot_polling, get_bot_token_status

app = FastAPI(title="Telegram Game Hub Platform")

# تسک پس‌زمینه‌ی پولینگ ربات تلگرام (روی startup ساخته و روی shutdown لغو می‌شود)
_bot_polling_task: Optional[asyncio.Task] = None

# ---------------------------------------------------------
# سیستم سطح (Level/XP) و قفل‌های وابسته به سطح برای آواتار/تغییر نام
# ---------------------------------------------------------
LEVEL_XP_STEP = 100                # هر ۱۰۰ XP یک سطح بالا می‌رود
XP_PER_GAME_PLAYED = 10
XP_WIN_BONUS = 15                  # روی XP_PER_GAME_PLAYED جمع می‌شود (برنده جمعاً ۲۵ می‌گیرد)
XP_DAILY_CLAIM = 5

AVATAR_ICON_LEVEL_REQUIRED = 3
AVATAR_ICON_COST = 50
AVATAR_PHOTO_LEVEL_REQUIRED = 7
AVATAR_PHOTO_COST = 200
AVAILABLE_AVATAR_ICONS = ["🃏", "👑", "💎", "🦁", "🎲", "🔥", "⚜️", "🐉", "🎭"]

USERNAME_FREE_COOLDOWN_DAYS = 7
USERNAME_CHANGE_COST = 150         # هزینه‌ی تغییر نام زودتر از یک هفته


def _award_xp(user: User, amount: int):
    user.xp = (user.xp or 0) + amount
    user.level = user.xp // LEVEL_XP_STEP + 1


def _serialize_user(user: User) -> dict:
    now = datetime.utcnow()
    can_change_free = (
        user.last_username_change is None
        or (now - user.last_username_change) >= timedelta(days=USERNAME_FREE_COOLDOWN_DAYS)
    )
    xp = user.xp or 0
    return {
        "id": user.id,
        "first_name": user.first_name,
        "coins": user.coins,
        "gems": user.gems,
        "is_admin": user.is_admin,
        "card_back_skin": user.card_back_skin,
        "avatar_skin": user.avatar_skin,
        "avatar_photo_url": user.avatar_photo_url,
        "level": user.level or 1,
        "xp": xp,
        "xp_progress": xp % LEVEL_XP_STEP,
        "xp_to_next_level": LEVEL_XP_STEP,
        "can_change_username_free": can_change_free,
        "username_change_cost": USERNAME_CHANGE_COST,
        "avatar_icon_unlock_level": AVATAR_ICON_LEVEL_REQUIRED,
        "avatar_photo_unlock_level": AVATAR_PHOTO_LEVEL_REQUIRED,
        "available_avatar_icons": AVAILABLE_AVATAR_ICONS,
    }

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
    if get_bot_token_status() == "missing":
        print(
            "\n"
            "!!! هشدار: متغیر محیطی BOT_TOKEN تنظیم نشده — ربات تلگرام به\n"
            "!!! پیام‌هایی مثل /start جواب نخواهد داد (فقط خود وب‌اپ کار\n"
            "!!! می‌کند). برای فعال‌سازی، BOT_TOKEN را در Environment ست کنید.\n"
        )
        return
    global _bot_polling_task
    # پولینگ ربات به‌صورت یک تسک پس‌زمینه‌ی جدا اجرا می‌شود تا مسدود شدنش
    # جلوی بالا آمدن خود سرور وب (FastAPI/uvicorn) را نگیرد.
    _bot_polling_task = asyncio.create_task(start_bot_polling())


@app.on_event("shutdown")
async def on_shutdown():
    if _bot_polling_task is not None:
        await stop_bot_polling()
        _bot_polling_task.cancel()


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


def _award_game_xp(db: Session, room):
    """به همه‌ی بازیکنان یک بازی تمام‌شده XP می‌دهد (برنده کمی بیشتر).
    مستقل از _payout_winner چون باید حتی برای بازی‌های بدون شرط‌بندی
    (min_bet=0) هم اجرا شود."""
    if room.xp_awarded:
        return
    room.xp_awarded = True

    winner_id = room.engine.winner
    user_ids = [int(uid) for uid in room.engine.players]
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    for u in users:
        amount = XP_PER_GAME_PLAYED + (XP_WIN_BONUS if str(u.id) == str(winner_id) else 0)
        _award_xp(u, amount)
    db.commit()


async def _finish_game_and_notify(room):
    """پرداخت جایزه + XP و اطلاع‌رسانی پایان بازی؛ چه بازی با حرکت یک
    بازیکن واقعی تمام شده باشد چه با حرکت هوش مصنوعیِ جایگزینِ او."""
    db = SessionLocal()
    try:
        _payout_winner(db, room)
        _award_game_xp(db, room)
    finally:
        db.close()
    await ws_manager.broadcast_raw(room, {"type": "GAME_OVER", "winner": room.engine.winner})


async def _run_bots_and_maybe_finish(room):
    """اگر نوبتِ فعلی به یک بازیکنِ bot_controlled (قطع‌شده) رسیده باشد،
    هوش مصنوعی به‌جای او (و هر بازیکنِ قطع‌شده‌ی بعدی در زنجیره) حرکت
    می‌کند تا نوبت به یک بازیکنِ واقعی برسد یا بازی تمام شود؛ در هر دو
    حالت وضعیت جدید برای همه broadcast و در صورت پایان بازی جایزه/XP
    پرداخت می‌شود."""
    bot_results = await ws_manager.run_bot_turns(room)
    if not bot_results:
        return
    await ws_manager.broadcast_state(room)
    if room.engine.is_finished and not room.payout_done:
        await _finish_game_and_notify(room)


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
                await _finish_game_and_notify(room)
            elif not room.engine.is_finished:
                # اگر بعد از این اکشن، نوبت به بازیکنِ قطع‌شده‌ای (bot_controlled)
                # رسیده باشد (مثلاً حریفش هنوز آنلاین است ولی او قبلاً قطع شده)،
                # هوش مصنوعی بلافاصله به‌جایش بازی می‌کند.
                await _run_bots_and_maybe_finish(room)

    except WebSocketDisconnect:
        ws_manager.disconnect(room, user_id)
        await ws_manager.broadcast_raw(room, {"type": "PLAYER_LEFT", "user_id": user_id})
        # از این لحظه ممکن است نوبت به همین بازیکنِ تازه‌قطع‌شده (یا حتی
        # زنجیره‌ای از چند بازیکنِ قطع‌شده‌ی قبلی) برسد؛ به‌جایشان بازی می‌کنیم
        # تا بازی هیچ‌وقت روی همان نفرات باقی‌مانده گیر نکند.
        await _run_bots_and_maybe_finish(room)


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

    return {"token": token, **_serialize_user(user)}


@app.get("/api/user/me")
async def get_me(user: User = Depends(get_current_user)):
    return _serialize_user(user)


# ---------------------------------------------------------
# REST API - تغییر نام نمایشی (یک‌بار در هفته رایگان، بیشتر از اون هزینه دارد)
# ---------------------------------------------------------
class UsernamePayload(BaseModel):
    new_name: str


@app.post("/api/user/username")
async def change_username(
    payload: UsernamePayload,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    async with _get_user_lock(user.id):
        db.refresh(user)
        new_name = payload.new_name.strip()

        if not (2 <= len(new_name) <= 24):
            raise HTTPException(status_code=400, detail="نام باید بین ۲ تا ۲۴ کاراکتر باشد.")
        if "\n" in new_name or "\r" in new_name or "\t" in new_name:
            raise HTTPException(status_code=400, detail="نام نامعتبر است.")

        now = datetime.utcnow()
        free = (
            user.last_username_change is None
            or (now - user.last_username_change) >= timedelta(days=USERNAME_FREE_COOLDOWN_DAYS)
        )

        if not free:
            if user.coins < USERNAME_CHANGE_COST:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"تغییر نام زودتر از یک هفته از آخرین بار، {USERNAME_CHANGE_COST} "
                        f"سکه هزینه دارد و سکه‌ی کافی ندارید."
                    ),
                )
            user.coins -= USERNAME_CHANGE_COST
            db.add(Transaction(user_id=user.id, amount=-USERNAME_CHANGE_COST, type="USERNAME_CHANGE"))

        user.first_name = new_name
        user.last_username_change = now
        db.commit()

        return {
            "status": "success",
            "message": "نام با موفقیت تغییر کرد." if free else f"نام تغییر کرد ({USERNAME_CHANGE_COST} سکه کسر شد).",
            **_serialize_user(user),
        }


# ---------------------------------------------------------
# REST API - آواتار (آیکون از سطح ۳، عکس پروفایل تلگرام از سطح ۷)
# ---------------------------------------------------------
class AvatarIconPayload(BaseModel):
    icon: str


@app.post("/api/user/avatar/icon")
async def set_avatar_icon(
    payload: AvatarIconPayload,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    async with _get_user_lock(user.id):
        db.refresh(user)

        if payload.icon == "default":
            user.avatar_skin = "default"
            db.commit()
            return {"status": "success", **_serialize_user(user)}

        if payload.icon not in AVAILABLE_AVATAR_ICONS:
            raise HTTPException(status_code=400, detail="آیکون نامعتبر است.")
        if (user.level or 1) < AVATAR_ICON_LEVEL_REQUIRED:
            raise HTTPException(
                status_code=403,
                detail=f"این قابلیت از سطح {AVATAR_ICON_LEVEL_REQUIRED} باز می‌شود.",
            )
        if user.coins < AVATAR_ICON_COST:
            raise HTTPException(status_code=400, detail="سکه‌ی کافی ندارید.")

        user.coins -= AVATAR_ICON_COST
        user.avatar_skin = payload.icon
        db.add(Transaction(user_id=user.id, amount=-AVATAR_ICON_COST, type="AVATAR_CHANGE"))
        db.commit()
        return {"status": "success", **_serialize_user(user)}


class AvatarPhotoPayload(BaseModel):
    photo_url: str


@app.post("/api/user/avatar/photo")
async def set_avatar_photo(
    payload: AvatarPhotoPayload,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    async with _get_user_lock(user.id):
        db.refresh(user)
        url = payload.photo_url.strip()

        if not url.startswith("https://"):
            raise HTTPException(status_code=400, detail="آدرس عکس نامعتبر است.")
        if (user.level or 1) < AVATAR_PHOTO_LEVEL_REQUIRED:
            raise HTTPException(
                status_code=403,
                detail=f"این قابلیت از سطح {AVATAR_PHOTO_LEVEL_REQUIRED} باز می‌شود.",
            )
        if user.coins < AVATAR_PHOTO_COST:
            raise HTTPException(status_code=400, detail="سکه‌ی کافی ندارید.")

        user.coins -= AVATAR_PHOTO_COST
        user.avatar_photo_url = url
        db.add(Transaction(user_id=user.id, amount=-AVATAR_PHOTO_COST, type="AVATAR_PHOTO"))
        db.commit()
        return {"status": "success", **_serialize_user(user)}


@app.post("/api/user/avatar/photo/clear")
async def clear_avatar_photo(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.avatar_photo_url = None
    db.commit()
    return {"status": "success", **_serialize_user(user)}


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
        _award_xp(user, XP_DAILY_CLAIM)
        db.add(Transaction(user_id=user.id, amount=reward_coins, type="DAILY_REWARD"))
        db.commit()

        return {"status": "success", "new_coins": user.coins, "reward": reward_coins, **_serialize_user(user)}


@app.post("/api/economy/spin-wheel")
async def spin_wheel(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    async with _get_user_lock(user.id):
        db.refresh(user)
        cost = 100
        if user.coins < cost:
            raise HTTPException(status_code=400, detail="سکه کافی ندارید!")

        user.coins -= cost

        # جوایز با احتمال وزن‌دار، نه یکسان. نسخه‌ی قبلی هر ۶ جایزه رو با
        # شانس مساوی انتخاب می‌کرد و میانگین برد هر چرخش ~۲۱۶ سکه سود خالص
        # بود (یعنی گردونه یه ماشین تضمینی پول‌سازی بود، نه شانسی). این
        # نسخه مثل یه گردونه‌ی واقعی طراحی شده: جوایز کوچیک محتمل‌تر،
        # جوایز بزرگ کمیاب، و میانگین کلی کمی به ضرر بازیکنه (~۲۰٪-)
        # تا با اسپم‌کردن نتونه سکه‌ی نامحدود تولید کنه.
        prize_options = [0, 30, 80, 150, 400, 1000, "skin_gold"]
        prize_weights = [35, 25, 20, 12, 6, 1.5, 0.5]
        won = random.choices(prize_options, weights=prize_weights, k=1)[0]

        if isinstance(won, int):
            user.coins += won
            if won == 0:
                msg = "شانس این دور باهات یار نبود. دوباره امتحان کن!"
            else:
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
