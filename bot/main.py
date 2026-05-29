from __future__ import annotations

import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from . import db, handlers, scheduler
from .config import TELEGRAM_TOKEN

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bot")


def main() -> None:
    db.init()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", handlers.start_cmd))
    application.add_handler(CommandHandler("help", handlers.help_cmd))
    application.add_handler(CommandHandler("report", handlers.report_cmd))
    application.add_handler(CommandHandler("sheet", handlers.sheet_cmd))
    application.add_handler(CommandHandler("remind", handlers.remind_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))
    application.add_handler(CallbackQueryHandler(handlers.on_picker_callback, pattern=r"^(day:|picker:|mode:)"))

    scheduler.register_jobs(application)

    logger.info("Application started; entering long-polling loop")
    application.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
