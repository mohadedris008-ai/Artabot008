import os
import logging
from datetime import datetime

from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Boolean, DateTime, ForeignKey, text, inspect
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger("game_platform.database")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./game_platform.db")

# هشدار: اگر DATABASE_URL روی sqlite (پیش‌فرض) باقی بمونه و پلتفرم میزبانی
# (مثل بسیاری از سرویس‌های PaaS رایگان) دیسک پایداری بین ری‌استارت‌ها نداشته
# باشه، فایل دیتابیس با هر دیپلوی/ری‌استارت پاک می‌شه و همه‌ی کاربرها انگار
# اولین‌بارشونه از صفر شروع می‌کنن. برای production حتماً یک DATABASE_URL
# با یک دیتابیس پایدار (مثلاً Postgres که اکثر پلتفرم‌ها به‌صورت رایگان/ارزان
# می‌دن) ست کنید.
if DATABASE_URL.startswith("sqlite") and os.getenv("ENV", "development") == "production":
    logger.warning(
        "SQLite در محیط production استفاده می‌شود. اگر دیسک این سرویس پایدار "
        "نباشد (رایج در بسیاری از PaaS ها)، داده‌ها با هر ری‌استارت/دیپلوی از "
        "بین می‌روند. یک DATABASE_URL با Postgres/MySQL پایدار تنظیم کنید."
    )

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
# pool_pre_ping: قبل از استفاده از هر کانکشن ذخیره‌شده در pool، یک پینگ سبک
# می‌زند تا مطمئن شود کانکشن هنوز زنده است. بدون این، وقتی سرویس دیتابیس
# (مثل Neon که سرورلس است) یک کانکشن idle را از سمت خودش می‌بندد،
# SQLAlchemy همچنان فکر می‌کند کانکشن سالم است و اولین کوئری بعد از یک
# دوره‌ی سکوت با خطای "SSL connection has been closed unexpectedly" شکست
# می‌خورد.
# pool_recycle: هر کانکشنی که عمرش از این مقدار (ثانیه) بیشتر شود، قبل از
# استفاده‌ی مجدد بسته و با یک کانکشن تازه جایگزین می‌شود؛ این هم از بسته
# شدن ناگهانی کانکشن‌های خیلی قدیمی توسط سرور دیتابیس جلوگیری می‌کند.
engine_kwargs = {"connect_args": connect_args}
if not DATABASE_URL.startswith("sqlite"):
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_recycle"] = 280
engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)  # Telegram user id
    first_name = Column(String, default="Gamer")
    username = Column(String, nullable=True)

    coins = Column(Integer, default=1000)
    gems = Column(Integer, default=0)

    is_admin = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)

    card_back_skin = Column(String, default="classic")
    avatar_skin = Column(String, default="default")     # آیکون آواتار انتخابی (سطح ۳ به بالا)
    avatar_photo_url = Column(String, nullable=True)     # عکس پروفایل تلگرام (سطح ۷ به بالا)

    xp = Column(Integer, default=0)
    level = Column(Integer, default=1)

    last_daily_claim = Column(DateTime, nullable=True)
    last_username_change = Column(DateTime, nullable=True)
    referred_by = Column(BigInteger, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), index=True)
    amount = Column(Integer)
    type = Column(String)  # DAILY_REWARD, REFERRAL, WHEEL, GAME_WIN, GAME_BET, AVATAR_CHANGE, XP, ...
    created_at = Column(DateTime, default=datetime.utcnow)


class AISettings(Base):
    """
    یک ردیف تکی (singleton، id همیشه ۱) که مالک از پنل مدیریت کنترلش
    می‌کند: آیا حریفِ حالت «بازی با سیستم» با قوانین ساده‌ی محلی
    (rule_based؛ همان get_bot_action در game_logic.py) بازی کند یا با
    یک سرویس هوش مصنوعی خارجی (api). آدرس‌ها/کلیدهای API به‌صورت یک
    رشته‌ی JSON ذخیره می‌شوند (نه ستون‌های جدا) تا مالک بتواند چند API
    پشتیبان پشت‌سرهم تعریف کند؛ اگر یکی جواب نداد/خطا داد، بعدی امتحان
    می‌شود (به همین دلیل لیست است، نه یک مقدار تکی).
    """
    __tablename__ = "ai_settings"

    id = Column(Integer, primary_key=True, default=1)
    mode = Column(String, default="rule_based")  # "rule_based" یا "api"
    # JSON: [{"name": "...", "url": "...", "api_key": "..."}, ...]
    api_endpoints_json = Column(String, default="[]")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def _run_light_migrations():
    """
    این پروژه از Alembic استفاده نمی‌کنه، و create_all() فقط جدول‌های
    غایب رو می‌سازه، نه ستون‌های جدیدی که به یک جدول از قبل موجود اضافه
    شدن. برای اینکه کاربرهای قدیمی روی یک دیتابیس already-deployed خراب
    نشن، ستون‌های جدید رو دستی و ایمن (IF NOT EXISTS) اضافه می‌کنیم.
    """
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return  # جدول تازه با create_all ساخته میشه و از اول ستون‌ها رو داره

    existing_cols = {c["name"] for c in inspector.get_columns("users")}
    # TIMESTAMP (نه DATETIME) چون DATETIME یک نوع معتبر در PostgreSQL نیست؛
    # SQLite هر اسم نوعی رو قبول می‌کنه ولی Postgres سخت‌گیره.
    new_columns = {
        "avatar_photo_url": "VARCHAR",
        "xp": "INTEGER DEFAULT 0",
        "level": "INTEGER DEFAULT 1",
        "last_username_change": "TIMESTAMP",
    }
    for col_name, col_type in new_columns.items():
        if col_name in existing_cols:
            continue
        # هر ستون توی تراکنش/اتصال جدای خودش اضافه می‌شه؛ چون توی
        # PostgreSQL (برخلاف SQLite) یک ALTER ناموفق کل تراکنش رو
        # abort می‌کنه و دستورهای بعدی همون تراکنش هم شکست می‌خورن.
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"))
            logger.info(f"migration: ستون '{col_name}' به جدول users اضافه شد")
        except Exception as e:
            logger.warning(f"migration ستون '{col_name}' ناموفق بود: {e}")


def init_db():
    Base.metadata.create_all(bind=engine)
    _run_light_migrations()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
