from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    full_name TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    text TEXT,
    message_type TEXT NOT NULL,
    text_length INTEGER NOT NULL DEFAULT 0,
    word_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_created_at
    ON messages(created_at);

CREATE INDEX IF NOT EXISTS idx_messages_chat_created_at
    ON messages(chat_id, created_at);
"""

_connection: sqlite3.Connection | None = None


def init_db(db_path: Path) -> None:
    """Initialize SQLite database and create required tables."""
    global _connection
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _connection = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    _connection.row_factory = sqlite3.Row
    _connection.executescript(DB_SCHEMA)
    _connection.commit()


def close_db() -> None:
    """Close active SQLite connection, if initialized."""
    global _connection
    if _connection is None:
        return

    _connection.close()
    _connection = None


def save_message(
    *,
    chat_id: int,
    user_id: int,
    username: str | None,
    full_name: str,
    message_id: int,
    text: str | None,
    message_type: str,
    text_length: int,
    word_count: int,
    created_at: datetime | None = None,
) -> int:
    """Insert a message row and return new record id."""
    conn = _require_connection()
    timestamp = (created_at or datetime.utcnow()).replace(microsecond=0).isoformat(
        sep=" "
    )

    cursor = conn.execute(
        """
        INSERT INTO messages (
            chat_id, user_id, username, full_name, message_id,
            text, message_type, text_length, word_count, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            user_id,
            username,
            full_name,
            message_id,
            text,
            message_type,
            text_length,
            word_count,
            timestamp,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_messages_by_date_range(
    start_date: date | datetime | str,
    end_date: date | datetime | str,
) -> list[dict[str, Any]]:
    """
    Return messages where DATE(created_at) is between start_date and end_date (inclusive).
    Expected string format: YYYY-MM-DD.
    """
    conn = _require_connection()
    start = _as_iso_date(start_date)
    end = _as_iso_date(end_date)

    rows = conn.execute(
        """
        SELECT
            id, chat_id, user_id, username, full_name, message_id,
            text, message_type, text_length, word_count, created_at
        FROM messages
        WHERE DATE(created_at) BETWEEN DATE(?) AND DATE(?)
        ORDER BY created_at ASC
        """,
        (start, end),
    ).fetchall()
    return [dict(row) for row in rows]


def get_messages_by_day(day_value: date | datetime | str) -> list[dict[str, Any]]:
    """
    Return messages for a single day by DATE(created_at).
    Expected string format: YYYY-MM-DD.
    """
    conn = _require_connection()
    day = _as_iso_date(day_value)

    rows = conn.execute(
        """
        SELECT
            id, chat_id, user_id, username, full_name, message_id,
            text, message_type, text_length, word_count, created_at
        FROM messages
        WHERE DATE(created_at) = DATE(?)
        ORDER BY created_at ASC
        """,
        (day,),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_messages_older_than(days: int) -> int:
    """Delete rows older than N days and return affected row count."""
    if days < 0:
        raise ValueError("days must be >= 0")

    conn = _require_connection()
    cursor = conn.execute(
        """
        DELETE FROM messages
        WHERE created_at < DATETIME('now', ?)
        """,
        (f"-{days} days",),
    )
    conn.commit()
    return int(cursor.rowcount)


def _require_connection() -> sqlite3.Connection:
    if _connection is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")
    return _connection


def _as_iso_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value
