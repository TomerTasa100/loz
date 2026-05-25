from __future__ import annotations

import logging
from datetime import time

from telegram.ext import Application, ContextTypes

from . import db, reporter
from .config import TELEGRAM_CHAT_ID
from .week import TZ, DAY_LABEL_HE, upcoming_week

logger = logging.getLogger(__name__)

KICKOFF_TEXT = (
    "שלום לכולם 👋\n"
    "הגיע הזמן לרשום משמרות לשבוע הבא.\n"
    "פשוט שלחו הודעה עם הימים והשעות, למשל: \"ראשון 14:00-19:00, שלישי 18-22\".\n"
    "אפשר גם בפרטי אליי."
)

LASTCALL_TEXT = (
    "תזכורת אחרונה לרישום משמרות! ⏰\n"
    "הדו\"ח הסופי יישלח הלילה ב-23:00.\n"
    "מי שלא רשם — זה הזמן."
)


async def kickoff(context: ContextTypes.DEFAULT_TYPE) -> None:
    week = upcoming_week()
    db.ensure_week(week)
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=KICKOFF_TEXT)


async def lastcall(context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=LASTCALL_TEXT)


async def nudge(context: ContextTypes.DEFAULT_TYPE) -> None:
    week = upcoming_week()
    db.ensure_week(week)
    missing = db.users_without_submission(week.id)

    dm_failed: list[dict] = []
    for user in missing:
        if user["dm_chat_id"]:
            try:
                await context.bot.send_message(
                    chat_id=user["dm_chat_id"],
                    text=(
                        "היי! עוד לא רשמת משמרת לשבוע הקרוב.\n"
                        "שלח/י לי כאן הודעה עם הימים והשעות, או בקבוצה."
                    ),
                )
            except Exception as e:
                logger.warning("Could not DM user %s: %s", user["user_id"], e)
                dm_failed.append(user)
        else:
            dm_failed.append(user)

    mentions: list[str] = []
    for user in dm_failed:
        if user["username"]:
            mentions.append(f"@{user['username']}")
    if mentions:
        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="עוד לא רשמתם משמרת השבוע: " + " ".join(mentions),
        )


async def report(context: ContextTypes.DEFAULT_TYPE) -> None:
    week = upcoming_week()
    db.ensure_week(week)
    text = reporter.build_report(week)
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)


JOBS_BY_NAME = {
    "kickoff": kickoff,
    "lastcall": lastcall,
    "nudge": nudge,
    "report": report,
}


def register_jobs(application: Application) -> None:
    jq = application.job_queue
    # PTB weekday: Mon=0 .. Sun=6
    jq.run_daily(kickoff, time=time(18, 0, tzinfo=TZ), days=(3,), name="kickoff")
    jq.run_daily(nudge, time=time(16, 0, tzinfo=TZ), days=(5,), name="nudge")
    jq.run_daily(lastcall, time=time(20, 0, tzinfo=TZ), days=(5,), name="lastcall")
    jq.run_daily(report, time=time(23, 0, tzinfo=TZ), days=(5,), name="report")
