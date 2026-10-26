"""Конфигурация бота. Читается один раз при старте."""
import json
import os
from pathlib import Path

BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN")

def _int_env(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default

ADMIN_CHAT_ID = _int_env("ADMIN_CHAT_ID", 0)

_admin_ids_str = os.getenv("ADMIN_USER_ID", "")
ADMIN_USER_IDS = []
for x in _admin_ids_str.split(","):
    x = x.strip()
    if x.isdigit():
        ADMIN_USER_IDS.append(int(x))

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"
LOG_PATH = DATA_DIR / "bot.log"

DAILY_AD_LIMIT = _int_env("DAILY_AD_LIMIT", 6)
MAX_PHOTOS = 5
MAX_TEXT_LEN = 1500
PHOTO_MAX_BYTES = 10 * 1024 * 1024
SESSION_TIMEOUT_MIN = 15
CAPTCHA_MAX_ATTEMPTS = 3
CAPTCHA_BAN_MIN = 10
RATE_LIMIT_SECONDS = 2.0

STOPWORDS = {
    "хуй", "хуя", "хуё", "хуе", "пизда", "пизды", "пизде", "блядь", "бля",
    "ебать", "ебал", "ебёт", "ебет", "ебан", "ебанат", "сука", "мразь",
    "пидор", "пидар", "гандон", "долбоеб", "долбоёб", "залупа", "мудак",
    "мудило", "шлюха", "проститутка",
}

def load_channels() -> list[dict]:
    raw = os.getenv("CHANNELS_JSON", "[]")
    try:
        channels = json.loads(raw)
        valid = []
        for ch in channels:
            if isinstance(ch, dict) and "name" in ch and "id" in ch:
                valid.append({"name": str(ch["name"]), "id": int(ch["id"])})
        if valid:
            return valid
    except Exception as e:
        print(f"⚠️ Ошибка парсинга CHANNELS_JSON: {e}")
    return [{"name": "Основной канал", "id": ADMIN_CHAT_ID}]
