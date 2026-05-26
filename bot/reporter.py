from __future__ import annotations

from . import db
from .week import DAY_KEYS, DAY_LABEL_HE, Week


def _display_name(row: dict) -> str:
    return row.get("display_name") or row.get("first_name") or (f"@{row['username']}" if row.get("username") else f"id:{row['user_id']}")


def build_report(week: Week) -> str:
    subs = db.latest_submissions_for_week(week.id)

    # Bucket shifts by day
    by_day: dict[str, list[tuple[str, str | None]]] = {k: [] for k in DAY_KEYS}
    submitter_ids: set[int] = set()
    for row in subs:
        submitter_ids.add(row["user_id"])
        name = _display_name(row)
        for shift in row["shifts"]:
            day = shift.get("day")
            if day in by_day:
                by_day[day].append((name, shift.get("time_range")))

    lines = [f"📋 משמרות לשבוע {week.label()}", ""]
    for day_key, day_date in week.dates():
        date_label = day_date.strftime("%d.%m")
        entries = by_day[day_key]
        if entries:
            lines.append(f"🟢 {DAY_LABEL_HE[day_key]} {date_label}")
            for name, _tr in entries:
                lines.append(f"  • {name}")
        else:
            lines.append(f"⚪ {DAY_LABEL_HE[day_key]} {date_label} — אין משמרות")
        lines.append("")

    missing = db.users_without_submission(week.id)
    if missing:
        names = []
        for u in missing:
            if u["username"]:
                names.append(f"@{u['username']}")
            elif u["first_name"]:
                names.append(u["first_name"])
        if names:
            lines.append("❌ לא נרשמו: " + ", ".join(names))

    return "\n".join(lines).rstrip()
