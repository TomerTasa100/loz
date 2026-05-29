"""Append the weekly schedule to a Google Sheet as an immutable historical archive.

Long format: one row per (person, day) shift. Re-runs for the same week_id
replace existing rows instead of duplicating, so admin `/remind report` is safe.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .config import (
    GOOGLE_SERVICE_ACCOUNT_JSON,
    GOOGLE_SHEETS_SPREADSHEET_ID,
    GOOGLE_SHEETS_WORKSHEET,
)
from .week import DAY_LABEL_HE, Week

logger = logging.getLogger(__name__)

HEADERS = [
    "week_id",
    "date",
    "day",
    "name",
    "employee_id",
    "time_range",
    "source",
    "submitted_at",
]

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_worksheet_cache: Any | None = None


def _enabled() -> bool:
    return bool(GOOGLE_SHEETS_SPREADSHEET_ID) and os.path.exists(GOOGLE_SERVICE_ACCOUNT_JSON)


def _worksheet() -> Any:
    global _worksheet_cache
    if _worksheet_cache is not None:
        return _worksheet_cache

    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_JSON, scopes=_SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(GOOGLE_SHEETS_SPREADSHEET_ID)
    try:
        ws = spreadsheet.worksheet(GOOGLE_SHEETS_WORKSHEET)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=GOOGLE_SHEETS_WORKSHEET, rows=1000, cols=len(HEADERS))

    first_row = ws.row_values(1)
    if first_row != HEADERS:
        ws.update("A1", [HEADERS], value_input_option="USER_ENTERED")

    _worksheet_cache = ws
    return ws


def _display_name(s: dict) -> str:
    return (
        s.get("display_name")
        or s.get("first_name")
        or (f"@{s['username']}" if s.get("username") else str(s["user_id"]))
    )


def _build_rows(week: Week, submissions: list[dict]) -> list[list[Any]]:
    date_by_day = {key: d.isoformat() for key, d in week.dates()}
    rows: list[list[Any]] = []
    for s in submissions:
        name = _display_name(s)
        for shift in s.get("shifts", []):
            day_key = shift.get("day", "")
            rows.append([
                week.id,
                date_by_day.get(day_key, ""),
                DAY_LABEL_HE.get(day_key, day_key),
                name,
                s.get("employee_id") or "",
                shift.get("time_range", ""),
                s.get("source", "") or "",
                s.get("created_at", "") or "",
            ])
    return rows


def _delete_rows_for_week(ws: Any, week_id: int) -> None:
    col_a = ws.col_values(1)  # includes header
    target = str(week_id)
    # Collect 1-indexed row numbers (skip header at row 1).
    to_delete = [i + 1 for i, val in enumerate(col_a) if i > 0 and val == target]
    # Delete bottom-up so indices don't shift.
    for row in reversed(to_delete):
        ws.delete_rows(row)


def sync_week(week: Week, submissions: list[dict]) -> None:
    if not _enabled():
        logger.info("Sheets sync disabled (missing config); skipping week %s", week.id)
        return
    ws = _worksheet()
    _delete_rows_for_week(ws, week.id)
    rows = _build_rows(week, submissions)
    if not rows:
        logger.info("No shifts to sync for week %s", week.id)
        return
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    logger.info("Synced %d shift rows to Google Sheets for week %s", len(rows), week.id)
