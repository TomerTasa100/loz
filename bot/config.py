import os


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


TELEGRAM_TOKEN = _required("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(_required("TELEGRAM_CHAT_ID"))
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
}
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
DB_PATH = os.environ.get("DB_PATH", "/data/shifts.db")
TIMEZONE = os.environ.get("TZ", "Asia/Jerusalem")
