"""
ربات تلگرام (aiogram) — پاسخ‌گویی به پیام‌های مستقیم کاربر در چت با ربات
------------------------------------------------------------------------
تا پیش از این، این پروژه فقط یک وب‌اپ (FastAPI) بود و هیچ کدی برای دریافت
و پاسخ به پیام‌های تلگرام (مثل /start) نداشت؛ دکمه‌ی بازکردن مینی‌اپ که کار
می‌کرد صرفاً یک تنظیم دستی در خود BotFather بود، نه چیزی که این ریپو بسازد.
این فایل همان بخش گم‌شده است: به /start و چند دستور ساده‌ی دیگر جواب می‌دهد
و یک دکمه‌ی «🎮 ورود به بازی» (Web App button) زیر پیام خوش‌آمدگویی نشان
می‌دهد که مستقیم مینی‌اپ را باز می‌کند.

نکته‌ی مهم درباره‌ی دیپلوی: پلن رایگان Render فقط یک نوع پردازه («web»)
اجرا می‌کند، پس نمی‌شود این ربات را با «worker: python bot.py» جداگانه در
Procfile اجرا کرد (نیاز به یک سرویس دوم/پولی دارد). به همین دلیل پولینگ
این ربات به‌عنوان یک تسک پس‌زمینه‌ی asyncio داخل همان پردازه‌ی FastAPI
(در main.py، رویداد startup) اجرا می‌شود؛ اگر بعداً به webhook مهاجرت شد،
فقط کافی است start_bot_polling این‌جا با یک ثبت webhook جایگزین شود.
"""

import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

logger = logging.getLogger("game_platform.bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
# آدرس عمومی مینی‌اپ (همانی که دکمه‌ی Web App تلگرام باز می‌کند). اگر ست
# نشود، از آخرین آدرس شناخته‌شده‌ی دیپلوی روی Render استفاده می‌شود.
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://my-telegram-game-bot-ft9g.onrender.com")

router_dp: Dispatcher = Dispatcher()


def _game_open_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 ورود به بازی", web_app=WebAppInfo(url=MINI_APP_URL))]
    ])


WELCOME_TEXT = (
    "🃏 <b>به آرتا گیم خوش آمدید!</b>\n\n"
    "اینجا می‌تونید پاسور، حکم و سکوئنس رو با بقیه‌ی بازیکن‌ها انجام بدید، "
    "سکه و جم جمع کنید و توی کلوپ اشرافی سطح بگیرید.\n\n"
    "برای شروع، روی دکمه‌ی زیر بزنید:"
)

HELP_TEXT = (
    "❓ <b>راهنمای سریع</b>\n\n"
    "• /start — باز کردن دوباره‌ی پیام خوش‌آمدگویی و دکمه‌ی ورود به بازی\n"
    "• /help — همین راهنما\n\n"
    "راهنمای کامل هر بازی (قوانین، نحوه‌ی امتیازگیری) داخل خود مینی‌اپ، "
    "با زدن دکمه‌ی «؟» بالای هر میز در دسترسه."
)


@router_dp.message(CommandStart())
async def handle_start(message: Message):
    await message.answer(WELCOME_TEXT, reply_markup=_game_open_keyboard())


@router_dp.message(Command("help"))
async def handle_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=_game_open_keyboard())


@router_dp.message()
async def handle_fallback(message: Message):
    # هر پیام دیگری (متن آزاد، استیکر و ...) هم بی‌جواب نمی‌ماند؛ کاربر را
    # به همان دکمه‌ی ورود به بازی هدایت می‌کنیم.
    await message.answer(
        "برای بازی کردن روی دکمه‌ی زیر بزنید 👇 (یا /help رو بفرستید)",
        reply_markup=_game_open_keyboard(),
    )


_bot_instance: Bot | None = None


def get_bot_token_status() -> str:
    if not BOT_TOKEN:
        return "missing"
    return "configured"


_polling_started = False


async def start_bot_polling():
    """در main.py روی رویداد startup به‌صورت یک تسک پس‌زمینه فراخوانی می‌شود."""
    global _bot_instance, _polling_started
    if not BOT_TOKEN:
        logger.warning(
            "BOT_TOKEN تنظیم نشده؛ ربات تلگرام (/start و بقیه‌ی دستورها) غیرفعال "
            "می‌ماند — فقط خود وب‌اپ در دسترس است."
        )
        return
    _bot_instance = Bot(token=BOT_TOKEN)
    logger.info("شروع polling ربات تلگرام...")
    try:
        _polling_started = True
        # drop_pending_updates=True: پیام‌های قدیمی‌ای که هنگام آفلاین بودن
        # سرویس (مثلاً حین ری‌دیپلوی) جمع شده‌اند را نادیده می‌گیرد تا کاربر
        # با یک هجوم پیام قدیمی روبه‌رو نشود.
        # handle_signals=False: مدیریت SIGINT/SIGTERM را به عهده‌ی uvicorn
        # (که خودش این پردازه را اجرا کرده) می‌گذاریم؛ چون این پولینگ در یک
        # تسک پس‌زمینه (نه event loop اصلی/thread اصلی) اجرا می‌شود، تلاش
        # خود aiogram برای نصب signal handler با خطا مواجه می‌شد.
        await router_dp.start_polling(
            _bot_instance, drop_pending_updates=True, handle_signals=False
        )
    except Exception:
        logger.exception("پولینگ ربات تلگرام با خطا متوقف شد")
    finally:
        _polling_started = False


async def stop_bot_polling():
    global _bot_instance, _polling_started
    if _polling_started:
        try:
            await router_dp.stop_polling()
        except RuntimeError:
            # اگر پولینگ هیچ‌وقت واقعاً استارت نشده باشد (مثلاً BOT_TOKEN
            # نامعتبر بود و همان ابتدا خطا داد)، stop_polling خودش خطا
            # می‌دهد؛ چون فقط داریم منابع را پاک می‌کنیم، این قابل صرف‌نظر است.
            pass
    if _bot_instance is not None:
        await _bot_instance.session.close()
        _bot_instance = None
