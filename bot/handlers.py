from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ReactionEmoji
from telegram.ext import ContextTypes

from . import db, scheduler
from .config import ADMIN_IDS, TELEGRAM_CHAT_ID
from .parser import parse_shift
from .week import DAY_KEYS, DAY_LABEL_HE, Week, upcoming_week

logger = logging.getLogger(__name__)

# Selectable hours (24-hour clock). Arrival can be 06..23, departure can be arrival+1..24.
HOUR_MIN = 6
HOUR_MAX = 24
REQUIRED_WEEKLY_HOURS = 40

# Preset shift modes the user picks per day before any hour selection.
TEN_HOUR_LENGTH = 10
TEN_HOUR_MAX_ARRIVAL = HOUR_MAX - TEN_HOUR_LENGTH  # latest arrival that still fits 10h within HOUR_MAX

ID_PATTERN = re.compile(r"^\d{5,10}$")


def _slot_hours(time_range: str | None) -> int:
    if not time_range:
        return 0
    try:
        a, b = time_range.split("-")
        return max(0, int(b) - int(a))
    except ValueError:
        return 0


def _hours_total(chosen: dict[str, str | None]) -> int:
    return sum(_slot_hours(v) for v in chosen.values())


def _format_chosen(chosen: dict[str, str | None]) -> str:
    return ", ".join(
        f"{DAY_LABEL_HE[d]} {chosen[d]}" if chosen[d] else DAY_LABEL_HE[d]
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

    record = db.get_user(user.id)
    if not record or not record.get("display_name") or not record.get("employee_id"):
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
    context.user_data["arrival_pending"] = None
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
        if not context.user_data.get("time_remaining"):
            return
        if choice == "10h":
            context.user_data["shift_mode"] = "10h"
            await _prompt_arrival(query, context)
            return
        if choice == "specific":
            context.user_data["shift_mode"] = "specific"
            await _prompt_arrival(query, context)
            return
        return

    if data.startswith("arr:"):
        remaining = list(context.user_data.get("time_remaining") or [])
        chosen = dict(context.user_data.get("time_chosen") or {})
        mode = context.user_data.get("shift_mode")
        if not remaining or mode not in ("10h", "specific"):
            return
        try:
            hour = int(data.split(":", 1)[1])
        except ValueError:
            return
        current_day = remaining[0]

        if mode == "10h":
            if not HOUR_MIN <= hour <= TEN_HOUR_MAX_ARRIVAL:
                return
            chosen[current_day] = f"{hour:02d}-{hour + TEN_HOUR_LENGTH:02d}"
            context.user_data["time_remaining"] = remaining[1:]
            context.user_data["time_chosen"] = chosen
            context.user_data["shift_mode"] = None
            await _advance_or_finalize(query, context, user, chat, week)
            return

        # specific: store arrival, ask for departure
        if not HOUR_MIN <= hour < HOUR_MAX:
            return
        context.user_data["arrival_pending"] = hour
        await _prompt_departure(query, context, hour)
        return

    if data.startswith("dep:"):
        remaining = list(context.user_data.get("time_remaining") or [])
        chosen = dict(context.user_data.get("time_chosen") or {})
        arrival = context.user_data.get("arrival_pending")
        if not remaining or arrival is None:
            return
        try:
            hour = int(data.split(":", 1)[1])
        except ValueError:
            return
        if not arrival < hour <= HOUR_MAX:
            return
        current_day = remaining[0]
        chosen[current_day] = f"{arrival:02d}-{hour:02d}"
        context.user_data["time_remaining"] = remaining[1:]
        context.user_data["time_chosen"] = chosen
        context.user_data["arrival_pending"] = None
        context.user_data["shift_mode"] = None
        await _advance_or_finalize(query, context, user, chat, week)
        return


async def _advance_or_finalize(query, context: ContextTypes.DEFAULT_TYPE, user, chat, week: Week) -> None:
    if context.user_data.get("time_remaining"):
        await _prompt_mode(query, context)
        return

    chosen: dict[str, str | None] = context.user_data.get("time_chosen") or {}
    total = _hours_total(chosen)
    if total != REQUIRED_WEEKLY_HOURS:
        diff = total - REQUIRED_WEEKLY_HOURS
        sign = "יותר מדי" if diff > 0 else "פחות מדי"
        context.user_data["time_chosen"] = {}
        await query.edit_message_text(
            f"❌ סה\"כ {total} שעות — {sign} ב-{abs(diff)} שעות.\n"
            f"נדרשות בדיוק {REQUIRED_WEEKLY_HOURS} שעות בשבוע.\n"
            f"מה שבחרת: {_format_chosen(chosen)}\n\n"
            "שלח/י /start כדי להתחיל מחדש."
        )
        return

    ordered = [d for d in DAY_KEYS if d in chosen]
    parsed = [{"day": d, "time_range": chosen[d]} for d in ordered]
    raw = "\n".join(f"{DAY_LABEL_HE[d]} {chosen[d]}" for d in ordered)

    db.upsert_user(user.id, user.username, user.first_name, chat.id)
    db.ensure_week(week)
    db.add_submission(week.id, user.id, raw, parsed, "dm")

    context.user_data["selected_days"] = set()
    context.user_data["time_remaining"] = []
    context.user_data["time_chosen"] = {}

    await query.edit_message_text(
        f"✅ נרשמת ל-{REQUIRED_WEEKLY_HOURS} שעות:\n{raw}\n\n"
        "אפשר לשלוח /start בכל רגע כדי לעדכן."
    )


def _hour_keyboard(prefix: str, lo: int, hi: int) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(f"{h:02d}:00", callback_data=f"{prefix}:{h}") for h in range(lo, hi + 1)]
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    return InlineKeyboardMarkup(rows)


def _progress_header(chosen: dict[str, str | None]) -> str:
    if not chosen:
        return ""
    total = _hours_total(chosen)
    return f"עד כה: {_format_chosen(chosen)}\nסך שעות: {total}/{REQUIRED_WEEKLY_HOURS}\n\n"


async def _prompt_mode(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    remaining: list[str] = context.user_data.get("time_remaining") or []
    chosen: dict[str, str | None] = context.user_data.get("time_chosen") or {}
    if not remaining:
        return
    current_day = remaining[0]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{TEN_HOUR_LENGTH} שעות", callback_data="mode:10h")],
        [InlineKeyboardButton("שעה ספציפית", callback_data="mode:specific")],
    ])
    await query.edit_message_text(
        f"{_progress_header(chosen)}איזה סוג משמרת ליום {DAY_LABEL_HE[current_day]}?",
        reply_markup=keyboard,
    )


async def _prompt_arrival(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    remaining: list[str] = context.user_data.get("time_remaining") or []
    chosen: dict[str, str | None] = context.user_data.get("time_chosen") or {}
    mode = context.user_data.get("shift_mode")
    if not remaining:
        return
    current_day = remaining[0]
    if mode == "10h":
        subtitle = f"משמרת של {TEN_HOUR_LENGTH} שעות.\nשעת הגעה:"
        hi = TEN_HOUR_MAX_ARRIVAL
    else:
        subtitle = "שעה ספציפית.\nשעת הגעה:"
        hi = HOUR_MAX - 1
    await query.edit_message_text(
        f"{_progress_header(chosen)}{DAY_LABEL_HE[current_day]} — {subtitle}",
        reply_markup=_hour_keyboard("arr", HOUR_MIN, hi),
    )


async def _prompt_departure(query, context: ContextTypes.DEFAULT_TYPE, arrival: int) -> None:
    remaining: list[str] = context.user_data.get("time_remaining") or []
    chosen: dict[str, str | None] = context.user_data.get("time_chosen") or {}
    if not remaining:
        return
    current_day = remaining[0]
    await query.edit_message_text(
        f"{_progress_header(chosen)}{DAY_LABEL_HE[current_day]} — הגעה ב-{arrival:02d}:00\nשעת יציאה:",
        reply_markup=_hour_keyboard("dep", arrival + 1, HOUR_MAX),
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

    if source == "dm":
        step = context.user_data.get("onboarding_step")
        if step == "awaiting_name":
            name = msg.text.strip()
            if len(name) < 2:
                await msg.reply_text("שם קצר מדי. נסה/י שוב.")
                return
            context.user_data["onboarding_name"] = name
            context.user_data["onboarding_step"] = "awaiting_id"
            await msg.reply_text(f"נעים מאוד {name}!\nמה מספר הזהות / מספר עובד שלך? (ספרות בלבד)")
            return
        if step == "awaiting_id":
            emp_id = msg.text.strip()
            if not ID_PATTERN.match(emp_id):
                await msg.reply_text("המספר חייב להיות בין 5 ל-10 ספרות. נסה/י שוב.")
                return
            name = context.user_data.get("onboarding_name") or user.first_name or "משתמש"
            db.set_user_details(user.id, name, emp_id)
            context.user_data["onboarding_step"] = None
            context.user_data["onboarding_name"] = None
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
