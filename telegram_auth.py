import os
import json
import hmac
import hashlib
import urllib.parse
from typing import Optional

BOT_TOKEN = os.getenv("BOT_TOKEN", "7900000000:AAFAKE_TOKEN_FOR_DEV_PURPOSES_ONLY")

def verify_telegram_init_data(init_data: str) -> Optional[dict]:
    """اعتبارسنجی توکن و داده‌های ارسالی از مینی‌اپ تلگرام"""
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
