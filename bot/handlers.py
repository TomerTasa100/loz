from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatType, ReactionEmoji
from telegram.ext import ContextTypes

from . import db, scheduler
from .config import ADMIN_IDS, TELEGRAM_CHAT_ID
from .parser import parse_shift
from .week import DAY_LABEL_HE, upcoming_week

logger = logging.getLogger(__name__)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return
    dm_chat_id = chat.id if chat.type == ChatType.PRIVATE else None
    db.upsert_user(user.id, user.username, user.first_name, dm_chat_id)

    if chat.type != ChatType.PRIVATE:
        # In a group, just acknowledge.
        await update.effective_message.reply_text(
            "היי! דבר/י איתי בפרטי כדי שאוכל לשלוח לך תזכורות אישיות."
        )
        return

    await update.effective_message.reply_text(
        "שלום 👋\n"
        "אני בוט המשמרות. שלח/י לי כאן הודעה עם הימים והשעות, למשל:\n"
        "  ראשון 14:00-19:00, שלישי 18-22\n\n"
        "אזכיר בקבוצה ביום חמישי 18:00, אדרבן אותך אם לא רשמת עד שבת 16:00,\n"
        "ואת הדו\"ח הסופי אפרסם בקבוצה במוצאי שבת 23:00."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "פקודות זמינות:\n"
        "  /start — הרשמה לתזכורות אישיות\n"
        "  /help  — העזרה הזו\n\n"
        "פשוט כתוב/י הודעה רגילה עם ימים ושעות ואני אזהה אותה אוטומטית.\n"
        "ההודעה האחרונה שלך לכל שבוע היא הקובעת."
    )


async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or user.id not in ADMIN_IDS:
        return  # silent for non-admins
    args = context.args or []
    if not args or args[0] not in scheduler.JOBS_BY_NAME:
        await update.effective_message.reply_text(
            "שימוש: /remind kickoff|lastcall|nudge|report"
        )
        return
    job = scheduler.JOBS_BY_NAME[args[0]]
    await job(context)
    await update.effective_message.reply_text(f"בוצע: {args[0]}")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if msg is None or user is None or chat is None or not msg.text:
        return

    # Only listen in the configured group or in private chats.
    if chat.type == ChatType.PRIVATE:
        source = "dm"
        dm_chat_id = chat.id
    elif chat.id == TELEGRAM_CHAT_ID:
        source = "group"
        dm_chat_id = None
    else:
        return

    db.upsert_user(user.id, user.username, user.first_name, dm_chat_id)

    parsed = parse_shift(msg.text)
    if not parsed:
        return

    week = upcoming_week()
    db.ensure_week(week)
    db.add_submission(week.id, user.id, msg.text, parsed, source)

    if source == "dm":
        summary = ", ".join(
            f"{DAY_LABEL_HE[s['day']]} {s['time_range']}" if s["time_range"] else DAY_LABEL_HE[s["day"]]
            for s in parsed
        )
        await msg.reply_text(f"✅ נרשמת ל: {summary}")
    else:
        try:
            await context.bot.set_message_reaction(
                chat_id=chat.id,
                message_id=msg.message_id,
                reaction=[ReactionEmoji.THUMBS_UP],
            )
        except Exception as e:
            logger.debug("Could not react to message: %s", e)
