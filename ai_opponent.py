"""
انتخاب حرکت برای حریفِ «بازی با سیستم» (و جایگزینِ بازیکنِ قطع‌شده)
--------------------------------------------------------------------
دو حالت پشتیبانی می‌شود که مالک از پنل مدیریت بین‌شان سوییچ می‌کند:

1) rule_based: همان منطق ساده‌ی از قبل موجود در game_logic.py
   (BaseGame.get_bot_action) — سریع، رایگان، بدون وابستگی به شبکه.

2) api: حرکت از یک سرویس هوش مصنوعیِ خارجی (هر API سازگار با فرمت
   chat completions، مثل OpenAI/Groq/OpenRouter/...) گرفته می‌شود.
   مالک می‌تواند چند endpoint/کلید پشت‌سرهم تعریف کند؛ اگر یکی خطا داد
   یا timeout شد، بعدی امتحان می‌شود. اگر همه شکست خوردند یا پاسخ خارج
   از فهرست حرکت‌های قانونی بود، به rule_based سقوط می‌کنیم — یعنی بازی
   هیچ‌وقت به‌خاطر قطعی یک سرویس خارجی متوقف نمی‌ماند.

طراحی «فقط از بین حرکت‌های قانونی انتخاب کن» عمداً است: سرویس خارجی هیچ‌وقت
مستقیم قوانین بازی را اجرا نمی‌کند؛ فقط اندیسِ یکی از گزینه‌های از پیش
enumerate شده توسط خودِ سرور را برمی‌گرداند، پس حتی اگر مدل توهم بزند یا
فرمت عجیب برگرداند، بدترین حالت یک fallback به rule-based است، نه یک
حرکتِ غیرقانونی.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("game_platform.ai_opponent")

REQUEST_TIMEOUT_SECONDS = 8.0


async def _try_one_endpoint(
    endpoint: Dict[str, str],
    game_type: str,
    legal_actions: List[Dict[str, Any]],
    state_summary: Dict[str, Any],
) -> Optional[int]:
    """یک endpoint واحد را امتحان می‌کند و اندیسِ انتخاب‌شده در legal_actions
    را برمی‌گرداند (یا None اگر شکست خورد/نامعتبر بود)."""
    url = (endpoint.get("url") or "").strip()
    api_key = (endpoint.get("api_key") or "").strip()
    model = (endpoint.get("model") or "gpt-4o-mini").strip()
    if not url:
        return None

    options_text = "\n".join(
        f"{i}. {a.get('description', a.get('action'))}" for i, a in enumerate(legal_actions)
    )
    system_prompt = (
        "تو یک حریفِ هوش مصنوعی برای یک بازی رومیزی/کارتی هستی. فقط باید عدد "
        "اندیسِ بهترین حرکت از فهرست داده‌شده را برگردانی، هیچ متن اضافه‌ای ننویس."
    )
    user_prompt = (
        f"بازی: {game_type}\n"
        f"وضعیت فعلی (خلاصه): {json.dumps(state_summary, ensure_ascii=False)}\n\n"
        f"حرکت‌های قانونیِ موجود:\n{options_text}\n\n"
        "فقط عدد اندیسِ حرکتی که انتخاب می‌کنی را برگردان (مثلاً: 2)."
    )

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 10,
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            digits = "".join(ch for ch in content if ch.isdigit())
            if not digits:
                return None
            idx = int(digits)
            if 0 <= idx < len(legal_actions):
                return idx
            return None
    except Exception as e:
        logger.warning(f"AI endpoint '{endpoint.get('name', url)}' شکست خورد: {e}")
        return None


async def choose_action_via_api(
    endpoints: List[Dict[str, str]],
    game_type: str,
    legal_actions: List[Dict[str, Any]],
    state_summary: Dict[str, Any],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """endpoints را به ترتیب امتحان می‌کند تا یکی جواب معتبر بدهد. اگر همه
    شکست خوردند یا legal_actions خالی بود، None برمی‌گرداند (یعنی صدا زننده
    باید به rule-based سقوط کند)."""
    if not legal_actions:
        return None
    for endpoint in endpoints:
        idx = await _try_one_endpoint(endpoint, game_type, legal_actions, state_summary)
        if idx is not None:
            chosen = legal_actions[idx]
            return chosen["action"], chosen["payload"]
    return None
