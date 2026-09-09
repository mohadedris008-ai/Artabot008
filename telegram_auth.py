import os
import json
import time
import hmac
import hashlib
import urllib.parse
from typing import Optional

BOT_TOKEN = os.getenv("BOT_TOKEN")  # الزامی در پروداکشن، مقدار پیش‌فرض ندارد
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-production")
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7  # ۷ روز

# فقط وقتی صراحتاً DEV_MODE=1 ست شده باشد، حالت توسعه فعال می‌شود.
# دیگر هیچ حالت پیش‌فرض ناامنی وجود ندارد.
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"


def verify_telegram_init_data(init_data: str) -> Optional[dict]:
    """اعتبارسنجی امضای initData ارسالی از تلگرام طبق مستندات رسمی."""
    if DEV_MODE:
        return {"id": 12345678, "first_name": "بازیکن تست", "username": "test_user"}

    if not BOT_TOKEN:
        # اگر توکن ربات تنظیم نشده و DEV_MODE هم روشن نیست، هیچ ورودی معتبر نیست.
        return None

    if not init_data:
        return None

    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data, strict_parsing=True))
        if "hash" not in parsed_data:
            return None

        hash_check = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))

        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated_hash, hash_check):
            return None

        # جلوگیری از replay: رد کردن initData های قدیمی‌تر از ۲۴ ساعت
        auth_date = int(parsed_data.get("auth_date", "0"))
        if auth_date and (time.time() - auth_date) > 86400:
            return None

        user_data = json.loads(parsed_data.get("user", "{}"))
        return user_data
    except Exception:
        return None


# ---------------------------------------------------------
# توکن نشست ساده (HMAC-signed) — جایگزین ارسال initData/admin_id
# در هر درخواست. کلاینت این توکن را بعد از /api/user/sync دریافت
# و در هدر Authorization: Bearer <token> ارسال می‌کند.
# ---------------------------------------------------------

def create_session_token(user_id: int) -> str:
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{user_id}:{expires_at}"
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_session_token(token: str) -> Optional[int]:
    if not token:
        return None
    try:
        user_id_str, expires_at_str, signature = token.split(":")
        payload = f"{user_id_str}:{expires_at_str}"
        expected_signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_signature, signature):
            return None
        if int(expires_at_str) < time.time():
            return None
        return int(user_id_str)
    except Exception:
        return None
