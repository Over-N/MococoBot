import os
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "")
ADMIN_2FA_SECRET = os.getenv("ADMIN_2FA_SECRET", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
LOSTARK_API_KEY = os.getenv("LOSTARK_API_KEY", "")
LOSTARK_API_SUB1_KEY = os.getenv("LOSTARK_API_SUB1_KEY", "")
LOSTARK_API_SUB2_KEY = os.getenv("LOSTARK_API_SUB2_KEY", "")
SUPPORTER_CLASSES = ["바드", "도화가", "홀리나이트", "발키리"]

SERVER_LIST = [
    "카단", "카제로스", "니나브", "루페온",
    "실리안", "아만", "아브렐슈드", "카마인",
]

# Sticker / Webhook settings
STICKER_WEBHOOK_NAME = "Mococo Sticker Relay"
STICKER_USERNAME_SUFFIX = " · Mococo"
NO_RESIZE_KEYS = {"사다리", "단천해", "아만", "클라우디아", "뱌토"}
