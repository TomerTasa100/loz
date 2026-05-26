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

    # Per submitter: set of days they selected and their shift type per day.
    all_names: list[str] = []
    day_choice: dict[str, dict[str, str]] = {}  # name -> {day -> token}

    for row in subs:
        name = _display_name(row)
        all_names.append(name)
        day_choice[name] = {s["day"]: s.get("time_range", "") for s in row["shifts"]}

    dates = dict(week.dates())
    lines = ["דוח נוכחות - מחלקת מחקר ופיתוח:", ""]

    for day_key in REPORT_DAYS:
        d = dates[day_key]
        lines.append(f"{DAY_LABEL_FULL[day_key]} - {d.strftime('%d/%m/%y')}:")

        present = [n for n in all_names if day_choice[n].get(day_key) == "full"]
        remote  = [n for n in all_names if day_choice[n].get(day_key) != "full"]

        if present:
            lines.append("נוכחים:")
            for name in present:
                lines.append(f" {name}")

        if remote:
            lines.append("בבית:")
            for name in remote:
                lines.append(f" {name}")

        lines.append(SEPARATOR)
        lines.append("")

    return "\n".join(lines).rstrip()


def build_missing(week: Week) -> str | None:
    missing = db.users_without_submission(week.id)
    if not missing:
        return None
    names = []
    for u in missing:
        names.append(u.get("display_name") or u.get("first_name") or (f"@{u['username']}" if u.get("username") else f"id:{u['user_id']}"))
    return "לא שלחו משמרות:\n" + "\n".join(f" {n}" for n in names)
