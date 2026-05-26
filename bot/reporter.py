from __future__ import annotations

from . import db
from .week import DAY_KEYS, DAY_LABEL_HE, Week

SEPARATOR = "————————————————-"
REPORT_DAYS = [k for k in DAY_KEYS if k != "sunday"]
DAY_LABEL_FULL = {k: f"יום {DAY_LABEL_HE[k]}" for k in DAY_KEYS}


def _display_name(row: dict) -> str:
    return row.get("display_name") or row.get("first_name") or (f"@{row['username']}" if row.get("username") else f"id:{row['user_id']}")


def build_report(week: Week) -> str:
    subs = db.latest_submissions_for_week(week.id)

    # Build per-day buckets: present (full) and remote (half)
    present: dict[str, list[str]] = {k: [] for k in REPORT_DAYS}
    remote:  dict[str, list[str]] = {k: [] for k in REPORT_DAYS}

    dates = dict(week.dates())

    for row in subs:
        name = _display_name(row)
        for shift in row["shifts"]:
            day = shift.get("day")
            if day not in present:
                continue
            if shift.get("time_range") == "half":
                remote[day].append(name)
            else:
                present[day].append(name)

    lines = ["דוח נוכחות - מחלקת מחקר ופיתוח:", ""]

    for day_key in REPORT_DAYS:
        d = dates[day_key]
        lines.append(f"{DAY_LABEL_FULL[day_key]} - {d.strftime('%d/%m/%y')}:")

        if present[day_key]:
            lines.append("נוכחים:")
            for name in present[day_key]:
                lines.append(f" {name}")

        if remote[day_key]:
            lines.append("בבית:")
            for name in remote[day_key]:
                lines.append(f" {name}")

        lines.append(SEPARATOR)
        lines.append("")

    return "\n".join(lines).rstrip()
