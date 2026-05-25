from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import TIMEZONE

TZ = ZoneInfo(TIMEZONE)

# Canonical day keys, ordered Sun..Sat to match the work week.
DAY_KEYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]

DAY_LABEL_HE = {
    "sunday": "ראשון",
    "monday": "שני",
    "tuesday": "שלישי",
    "wednesday": "רביעי",
    "thursday": "חמישי",
    "friday": "שישי",
    "saturday": "שבת",
}


@dataclass(frozen=True)
class Week:
    id: int           # YYYYMMDD of the Sunday start
    start: date       # Sunday
    end: date         # Saturday

    def dates(self) -> list[tuple[str, date]]:
        return [(DAY_KEYS[i], self.start + timedelta(days=i)) for i in range(7)]

    def label(self) -> str:
        return f"{self.start.strftime('%d.%m')}–{self.end.strftime('%d.%m')}"


def now_local() -> datetime:
    return datetime.now(TZ)


def upcoming_week(reference: datetime | None = None) -> Week:
    """Return the Sun–Sat week that follows the upcoming Saturday report.

    Run any day Sun..Sat → returns the next Sun..Sat block. Run on Sunday itself
    → returns the week starting that Sunday.
    """
    ref = (reference or now_local()).date()
    # Python weekday: Mon=0..Sun=6. We want days until next Sunday (inclusive of today if Sunday).
    days_until_sunday = (6 - ref.weekday()) % 7
    sunday = ref + timedelta(days=days_until_sunday)
    saturday = sunday + timedelta(days=6)
    week_id = int(sunday.strftime("%Y%m%d"))
    return Week(id=week_id, start=sunday, end=saturday)
