from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType, ReactionEmoji
from telegram.ext import ContextTypes

from . import db, reporter, scheduler
from .config import ADMIN_IDS, GOOGLE_SHEETS_SPREADSHEET_ID, TELEGRAM_CHAT_ID
from .parser import parse_shift
from .week import DAY_KEYS, DAY_LABEL_HE, Week, upcoming_week

logger = logging.getLogger(__name__)

# Preset shift modes the user picks per day. Stored as the submission's time_range token.
FULL_DAY_TOKEN = "full"
HALF_DAY_TOKEN = "half"

DAY_VALUE = {FULL_DAY_TOKEN: 1.0, HALF_DAY_TOKEN: 0.5}
DAY_LABEL = {FULL_DAY_TOKEN: "יום מלא", HALF_DAY_TOKEN: "חצי יום"}

_MEMBER_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED}


async def _is_group_member(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=TELEGRAM_CHAT_ID, user_id=user_id)
        return member.status in _MEMBER_STATUSES
    except Exception:
        return False


def _day_value(token: str | None) -> float:
    return DAY_VALUE.get(token or "", 0.0)


def _days_total(chosen: dict[str, str | None]) -> float:
    return sum(_day_value(v) for v in chosen.values())


def _format_time_range(value: str | None) -> str:
    return DAY_LABEL.get(value or "", "")


def _format_total(total: float) -> str:
    return f"{total:g}"


def _format_chosen(chosen: dict[str, str | None]) -> str:
    return ", ".join(
        f"{DAY_LABEL_HE[d]} {_format_time_range(chosen[d])}".rstrip()
        for d in chosen
    )


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

    if not await _is_group_member(context.bot, user.id):
        await update.effective_message.reply_text("מצטער, הבוט זמין רק לחברי הקבוצה.")
        return

    record = db.get_user(user.id)
    if not record or not record.get("display_name"):
        context.user_data["onboarding_step"] = "awaiting_name"
        await update.effective_message.reply_text(
            "שלום 👋\nכדי להירשם, מה השם המלא שלך?"
        )
        return

    await _send_day_picker(update.effective_message.reply_text, context, greet_name=record["display_name"])


async def _send_day_picker(send, context: ContextTypes.DEFAULT_TYPE, *, greet_name: str) -> None:
    week = upcoming_week()
    context.user_data["selected_days"] = set()
    context.user_data["picker_week_id"] = week.id
    context.user_data["time_remaining"] = []
    context.user_data["time_chosen"] = {}
    await send(
        f"היי {greet_name} 👋\n"
        f"בחר/י את הימים שבהם תרצה/י לעבוד בשבוע {week.label()}:\n"
        "(לחיצה על יום מסמנת/מבטלת. בסיום לחצ/י \"סיום\".)",
        reply_markup=_days_keyboard(week, set()),
    )


def _days_keyboard(week: Week, selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for day_key, d in week.dates():
        label = f"{'✅ ' if day_key in selected else ''}{DAY_LABEL_HE[day_key]} {d.strftime('%d.%m')}"
        rows.append([InlineKeyboardButton(label, callback_data=f"day:{day_key}")])
    rows.append([
        InlineKeyboardButton("✖️ ניקוי", callback_data="picker:clear"),
        InlineKeyboardButton("סיום ✓", callback_data="picker:done"),
    ])
    return InlineKeyboardMarkup(rows)


async def on_picker_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None or chat.type != ChatType.PRIVATE:
        return

    selected: set[str] = context.user_data.get("selected_days") or set()
    week = upcoming_week()
    # Reset selection if a new week has rolled over since the picker opened.
    if context.user_data.get("picker_week_id") != week.id:
        selected = set()
        context.user_data["picker_week_id"] = week.id

    data = query.data
    if data.startswith("day:"):
        day_key = data.split(":", 1)[1]
        if day_key not in DAY_KEYS:
            return
        if day_key in selected:
            selected.remove(day_key)
        else:
            selected.add(day_key)
        context.user_data["selected_days"] = selected
        await query.edit_message_reply_markup(reply_markup=_days_keyboard(week, selected))
        return

    if data == "picker:clear":
        selected.clear()
        context.user_data["selected_days"] = selected
        await query.edit_message_reply_markup(reply_markup=_days_keyboard(week, selected))
        return

    if data == "picker:done":
        if not selected:
            await query.answer("לא נבחרו ימים", show_alert=False)
            return
        ordered = [d for d in DAY_KEYS if d in selected]
        context.user_data["time_remaining"] = ordered
        context.user_data["time_chosen"] = {}
        await _prompt_mode(query, context)
        return

    if data.startswith("mode:"):
        choice = data.split(":", 1)[1]
        if choice not in DAY_VALUE:
            return
        remaining = list(context.user_data.get("time_remaining") or [])
        chosen = dict(context.user_data.get("time_chosen") or {})
        if not remaining:
            return
        current_day = remaining[0]
        chosen[current_day] = choice
        context.user_data["time_remaining"] = remaining[1:]
        context.user_data["time_chosen"] = chosen
        await _advance_or_finalize(query, context, user, chat, week)
        return


async def _advance_or_finalize(query, context: ContextTypes.DEFAULT_TYPE, user, chat, week: Week) -> None:
    if context.user_data.get("time_remaining"):
        await _prompt_mode(query, context)
        return

    chosen: dict[str, str | None] = context.user_data.get("time_chosen") or {}
    ordered = [d for d in DAY_KEYS if d in chosen]
    parsed = [{"day": d, "time_range": chosen[d]} for d in ordered]
    raw = "\n".join(f"{DAY_LABEL_HE[d]} {_format_time_range(chosen[d])}" for d in ordered)
    total = _days_total(chosen)

    db.upsert_user(user.id, user.username, user.first_name, chat.id)
    db.ensure_week(week)
    db.add_submission(week.id, user.id, raw, parsed, "dm")

    context.user_data["selected_days"] = set()
    context.user_data["time_remaining"] = []
    context.user_data["time_chosen"] = {}

    await query.edit_message_text(
        f"✅ נרשמת ל-{_format_total(total)} ימים:\n{raw}\n\n"
        "אפשר לשלוח /start בכל רגע כדי לעדכן."
    )


def _progress_header(chosen: dict[str, str | None]) -> str:
    if not chosen:
        return ""
    total = _days_total(chosen)
    return f"עד כה: {_format_chosen(chosen)}\nסך ימים: {_format_total(total)}\n\n"


async def _prompt_mode(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    remaining: list[str] = context.user_data.get("time_remaining") or []
    chosen: dict[str, str | None] = context.user_data.get("time_chosen") or {}
    if not remaining:
        return
    current_day = remaining[0]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(DAY_LABEL[FULL_DAY_TOKEN], callback_data=f"mode:{FULL_DAY_TOKEN}")],
        [InlineKeyboardButton(DAY_LABEL[HALF_DAY_TOKEN], callback_data=f"mode:{HALF_DAY_TOKEN}")],
    ])
    await query.edit_message_text(
        f"{_progress_header(chosen)}איזה סוג משמרת ליום {DAY_LABEL_HE[current_day]}?",
        reply_markup=keyboard,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "פקודות זמינות:\n"
        "  /start — הרשמה לתזכורות אישיות\n"
        "  /help  — העזרה הזו\n\n"
        "פשוט כתוב/י הודעה רגילה עם ימים ושעות ואני אזהה אותה אוטומטית.\n"
        "ההודעה האחרונה שלך לכל שבוע היא הקובעת."
    )


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or chat.id != TELEGRAM_CHAT_ID:
        return
    week = upcoming_week()
    await update.effective_message.reply_text(reporter.build_report(week))
    missing = reporter.build_missing(week)
    if missing:
        await update.effective_message.reply_text(missing)


async def sheet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or chat.id != TELEGRAM_CHAT_ID:
        return
    if not GOOGLE_SHEETS_SPREADSHEET_ID:
        await update.effective_message.reply_text(
            "הגיליון עדיין לא הוגדר. צרו Google Sheet ושימו את ה-ID ב-.env בשם "
            "GOOGLE_SHEETS_SPREADSHEET_ID."
        )
        return
    url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEETS_SPREADSHEET_ID}/edit"
    await update.effective_message.reply_text(f"היסטוריית המשמרות:\n{url}")


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

    if source == "dm":
        if not await _is_group_member(context.bot, user.id):
            return
        step = context.user_data.get("onboarding_step")
        if step == "awaiting_name":
            name = msg.text.strip()
            if len(name) < 2:
                await msg.reply_text("שם קצר מדי. נסה/י שוב.")
                return
            db.set_user_details(user.id, name, "")
            context.user_data["onboarding_step"] = None
            await _send_day_picker(msg.reply_text, context, greet_name=name)
            return

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
