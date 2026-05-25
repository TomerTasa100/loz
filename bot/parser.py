from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from .config import OPENAI_API_KEY, OPENAI_MODEL
from .week import DAY_KEYS

logger = logging.getLogger(__name__)

# ---- Hebrew day vocabulary ---------------------------------------------------

_FULL_HE_TO_KEY = {
    "ראשון": "sunday",
    "שני": "monday",
    "שלישי": "tuesday",
    "רביעי": "wednesday",
    "חמישי": "thursday",
    "שישי": "friday",
    "שבת": "saturday",
}

_LETTER_HE_TO_KEY = {
    "א": "sunday",
    "ב": "monday",
    "ג": "tuesday",
    "ד": "wednesday",
    "ה": "thursday",
    "ו": "friday",
}

# Order matters: longer alternatives first so the regex engine picks them.
_DAY_PATTERN = re.compile(
    r"(?:יום\s+)?(ראשון|שני|שלישי|רביעי|חמישי|שישי)"
    r"|שבת"
    r"|יום\s+([אבגדהו])['׳]?"
    r"|(?<![\wא-ת])([אבגדהו])['׳]",
)

_TIME_RANGE_PATTERN = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*[-–—]\s*(\d{1,2})(?::(\d{2}))?"
)


@dataclass
class _DayHit:
    start: int
    end: int
    key: str


@dataclass
class _TimeHit:
    start: int
    end: int
    text: str  # normalized "HH:MM-HH:MM"


def _canonical_day(match: re.Match) -> str | None:
    full, letter_with_yom, letter_alone = match.group(1), match.group(2), match.group(3)
    if full:
        return _FULL_HE_TO_KEY.get(full)
    if letter_with_yom:
        return _LETTER_HE_TO_KEY.get(letter_with_yom)
    if letter_alone:
        return _LETTER_HE_TO_KEY.get(letter_alone)
    # Pure "שבת" branch — match.group(0) is the literal.
    if match.group(0).strip() == "שבת":
        return "saturday"
    return None


def _normalize_time_range(match: re.Match) -> str:
    h1, m1, h2, m2 = match.group(1), match.group(2), match.group(3), match.group(4)
    return f"{int(h1):02d}:{int(m1 or 0):02d}-{int(h2):02d}:{int(m2 or 0):02d}"


def parse_regex(text: str) -> list[dict] | None:
    """Try pure-regex parsing. Return list of {day, time_range} or None if no day found."""
    day_hits: list[_DayHit] = []
    for m in _DAY_PATTERN.finditer(text):
        key = _canonical_day(m)
        if key:
            day_hits.append(_DayHit(start=m.start(), end=m.end(), key=key))
    # The "שבת" alternative has no capturing group, so handle it separately.
    for m in re.finditer(r"שבת", text):
        # Avoid double-counting if already covered by _DAY_PATTERN (it is, but be safe).
        if not any(h.start == m.start() and h.end == m.end() for h in day_hits):
            day_hits.append(_DayHit(start=m.start(), end=m.end(), key="saturday"))
    day_hits.sort(key=lambda h: h.start)
    if not day_hits:
        return None

    time_hits = [
        _TimeHit(start=m.start(), end=m.end(), text=_normalize_time_range(m))
        for m in _TIME_RANGE_PATTERN.finditer(text)
    ]

    shifts: list[dict] = []
    for i, day in enumerate(day_hits):
        next_day_start = day_hits[i + 1].start if i + 1 < len(day_hits) else len(text)
        time_for_day: str | None = None
        for t in time_hits:
            if t.start >= day.end and t.end <= next_day_start:
                time_for_day = t.text
                break
        shifts.append({"day": day.key, "time_range": time_for_day})

    # Deduplicate (same day appearing twice): keep the first with a time, else the first.
    seen: dict[str, dict] = {}
    for s in shifts:
        key = s["day"]
        if key not in seen or (seen[key]["time_range"] is None and s["time_range"]):
            seen[key] = s
    return [seen[k] for k in DAY_KEYS if k in seen]


# ---- LLM fallback ------------------------------------------------------------

_LLM_SYSTEM = (
    "You extract shift submissions from Hebrew text. "
    "Return ONLY a JSON object with this exact shape: "
    '{"shifts":[{"day":"sunday|monday|tuesday|wednesday|thursday|friday|saturday",'
    '"time_range":"HH:MM-HH:MM"}]}. '
    "If a time range is not specified for a day, use null for time_range. "
    "If the message is not about shifts at all, return {\"shifts\":[]}. "
    "Do not include any prose, only JSON."
)


def _looks_shift_related(text: str) -> bool:
    """Cheap heuristic: any day word OR an explicit shift keyword."""
    if any(d in text for d in _FULL_HE_TO_KEY) or "שבת" in text:
        return True
    return bool(re.search(r"משמרת|משמרות|מגיע|להגיע|אגיע|יום\s+[אבגדהו]", text))


def parse_with_llm(text: str) -> list[dict] | None:
    if not OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI  # imported lazily so the bot runs without the SDK installed
    except ImportError:
        logger.warning("openai SDK not installed; skipping LLM fallback")
        return None
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=300,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": text},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Strip code fences if the model wrapped it anyway.
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S)
        data = json.loads(raw)
        shifts = data.get("shifts", [])
        cleaned: list[dict] = []
        for s in shifts:
            day = s.get("day")
            if day not in DAY_KEYS:
                continue
            tr = s.get("time_range")
            if tr is not None and not isinstance(tr, str):
                tr = None
            cleaned.append({"day": day, "time_range": tr})
        return cleaned or None
    except Exception as e:
        logger.warning("LLM parse failed: %s", e)
        return None


def parse_shift(text: str) -> list[dict] | None:
    """Return list of {day, time_range} or None if the message isn't a shift submission."""
    text = (text or "").strip()
    if not text:
        return None
    # Skip slash commands so /start etc. don't trigger parsing.
    if text.startswith("/"):
        return None

    regex_result = parse_regex(text)
    # If regex found day(s) AND at least one time, trust it without calling the LLM.
    if regex_result and any(s["time_range"] for s in regex_result):
        return regex_result

    if _looks_shift_related(text):
        llm_result = parse_with_llm(text)
        if llm_result:
            return llm_result

    # Regex found days but no times, and LLM was unavailable / unhelpful: still return regex hits.
    return regex_result
