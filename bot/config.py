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
REPORT_RECIPIENT_IDS = [
    int(x) for x in os.environ.get("REPORT_RECIPIENT_IDS", "").split(",") if x.strip()
]
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip() or None
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
DB_PATH = os.environ.get("DB_PATH", "/data/shifts.db")
TIMEZONE = os.environ.get("TZ", "Asia/Jerusalem")

GOOGLE_SHEETS_SPREADSHEET_ID = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip() or None
GOOGLE_SHEETS_WORKSHEET = os.environ.get("GOOGLE_SHEETS_WORKSHEET", "shifts").strip() or "shifts"
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "/data/service-account.json").strip()
