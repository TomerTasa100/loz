from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from .config import DB_PATH
from .week import Week

SCHEMA = """
CREATE TABLE IF NOT EXISTS weeks (
  id         INTEGER PRIMARY KEY,
  start_date TEXT NOT NULL,
  end_date   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  telegram_user_id INTEGER PRIMARY KEY,
  username         TEXT,
  first_name       TEXT,
  dm_chat_id       INTEGER,
  last_seen_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  week_id      INTEGER NOT NULL REFERENCES weeks(id),
  user_id      INTEGER NOT NULL REFERENCES users(telegram_user_id),
  raw_text     TEXT NOT NULL,
  parsed_json  TEXT NOT NULL,
  source       TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subs_week_user_created
  ON submissions(week_id, user_id, created_at DESC);
"""


def init() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with _conn() as c:
        c.executescript(SCHEMA)


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def ensure_week(week: Week) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO weeks (id, start_date, end_date) VALUES (?, ?, ?)",
            (week.id, week.start.isoformat(), week.end.isoformat()),
        )


def upsert_user(
    user_id: int,
    username: str | None,
    first_name: str | None,
    dm_chat_id: int | None,
) -> None:
    now = datetime.utcnow().isoformat(timespec="seconds")
    with _conn() as c:
        existing = c.execute(
            "SELECT dm_chat_id FROM users WHERE telegram_user_id = ?", (user_id,)
        ).fetchone()
        if existing is None:
            c.execute(
                """INSERT INTO users (telegram_user_id, username, first_name, dm_chat_id, last_seen_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, username, first_name, dm_chat_id, now),
            )
        else:
            # Keep an existing dm_chat_id if we don't have a new one to set.
            final_dm = dm_chat_id if dm_chat_id is not None else existing["dm_chat_id"]
            c.execute(
                """UPDATE users
                     SET username = ?, first_name = ?, dm_chat_id = ?, last_seen_at = ?
                   WHERE telegram_user_id = ?""",
                (username, first_name, final_dm, now, user_id),
            )


def add_submission(
    week_id: int,
    user_id: int,
    raw_text: str,
    parsed: list[dict],
    source: str,
) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO submissions (week_id, user_id, raw_text, parsed_json, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                week_id,
                user_id,
                raw_text,
                json.dumps(parsed, ensure_ascii=False),
                source,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )


def latest_submissions_for_week(week_id: int) -> list[dict]:
    """One row per user: their most recent submission for this week."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT s.user_id, s.raw_text, s.parsed_json, s.created_at,
                   u.username, u.first_name
            FROM submissions s
            JOIN users u ON u.telegram_user_id = s.user_id
            WHERE s.week_id = ?
              AND s.id IN (
                SELECT MAX(id) FROM submissions
                WHERE week_id = ?
                GROUP BY user_id
              )
            """,
            (week_id, week_id),
        ).fetchall()
    return [
        {
            "user_id": r["user_id"],
            "raw_text": r["raw_text"],
            "shifts": json.loads(r["parsed_json"]),
            "username": r["username"],
            "first_name": r["first_name"],
        }
        for r in rows
    ]


def users_without_submission(week_id: int) -> list[dict]:
    """Known users who haven't submitted anything for this week."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT telegram_user_id AS user_id, username, first_name, dm_chat_id
            FROM users
            WHERE telegram_user_id NOT IN (
                SELECT DISTINCT user_id FROM submissions WHERE week_id = ?
            )
            """,
            (week_id,),
        ).fetchall()
    return [dict(r) for r in rows]
