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

CREATE TABLE IF NOT EXISTS image_clusters (
    cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_hash TEXT NOT NULL,
    cluster_summary TEXT,
    usage_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_image_clusters_canonical_hash
    ON image_clusters(canonical_hash);

CREATE TABLE IF NOT EXISTS image_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    local_path TEXT,
    file_size INTEGER,
    context_text TEXT,
    phash TEXT,
    cluster_id INTEGER,
    ocr_text TEXT,
    summary_text TEXT,
    processing_status TEXT NOT NULL DEFAULT 'pending',
    processed_at TEXT,
    file_deleted INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(cluster_id) REFERENCES image_clusters(cluster_id)
);

CREATE INDEX IF NOT EXISTS idx_image_events_created_at
    ON image_events(created_at);

CREATE INDEX IF NOT EXISTS idx_image_events_cluster_id
    ON image_events(cluster_id);

CREATE INDEX IF NOT EXISTS idx_image_events_chat_message
    ON image_events(chat_id, message_id);

CREATE INDEX IF NOT EXISTS idx_image_events_cluster_created_at
    ON image_events(cluster_id, created_at);
"""

_connection: sqlite3.Connection | None = None


def init_db(db_path: Path) -> None:
    """Initialize SQLite database and create required tables."""
    global _connection
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _connection = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    _connection.row_factory = sqlite3.Row
    _connection.executescript(DB_SCHEMA)
    _ensure_column("image_events", "file_size", "INTEGER")
    _ensure_column("image_events", "context_text", "TEXT")
    _backfill_image_event_context(limit=25)
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


def create_image_event(
    *,
    message_id: int,
    chat_id: int,
    file_id: str,
    created_at: datetime,
    local_path: str | None,
    file_size: int | None = None,
    context_text: str | None = None,
) -> int:
    conn = _require_connection()
    timestamp = created_at.replace(microsecond=0).isoformat(sep=" ")
    cursor = conn.execute(
        """
        INSERT INTO image_events (
            message_id, chat_id, file_id, created_at, local_path,
            file_size, context_text, processing_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (message_id, chat_id, file_id, timestamp, local_path, file_size, context_text),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_image_clusters() -> list[dict[str, Any]]:
    conn = _require_connection()
    rows = conn.execute(
        """
        SELECT
            cluster_id, canonical_hash, cluster_summary, usage_count,
            first_seen_at, last_seen_at
        FROM image_clusters
        ORDER BY usage_count DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_top_image_clusters(limit: int = 10) -> list[dict[str, Any]]:
    conn = _require_connection()
    rows = conn.execute(
        """
        SELECT
            cluster_id, canonical_hash, cluster_summary, usage_count,
            first_seen_at, last_seen_at
        FROM image_clusters
        ORDER BY usage_count DESC, last_seen_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_image_cluster(cluster_id: int) -> dict[str, Any] | None:
    conn = _require_connection()
    row = conn.execute(
        """
        SELECT
            cluster_id, canonical_hash, cluster_summary, usage_count,
            first_seen_at, last_seen_at
        FROM image_clusters
        WHERE cluster_id = ?
        """,
        (cluster_id,),
    ).fetchone()
    return dict(row) if row else None


def create_image_cluster(
    *,
    canonical_hash: str,
    cluster_summary: str | None,
    seen_at: datetime,
) -> int:
    conn = _require_connection()
    timestamp = seen_at.replace(microsecond=0).isoformat(sep=" ")
    cursor = conn.execute(
        """
        INSERT INTO image_clusters (
            canonical_hash, cluster_summary, usage_count, first_seen_at, last_seen_at
        )
        VALUES (?, ?, 1, ?, ?)
        """,
        (canonical_hash, cluster_summary, timestamp, timestamp),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_image_cluster_usage(*, cluster_id: int, seen_at: datetime) -> None:
    conn = _require_connection()
    timestamp = seen_at.replace(microsecond=0).isoformat(sep=" ")
    conn.execute(
        """
        UPDATE image_clusters
        SET usage_count = usage_count + 1,
            last_seen_at = ?
        WHERE cluster_id = ?
        """,
        (timestamp, cluster_id),
    )
    conn.commit()


def update_image_cluster_summary(*, cluster_id: int, cluster_summary: str) -> None:
    conn = _require_connection()
    conn.execute(
        """
        UPDATE image_clusters
        SET cluster_summary = ?
        WHERE cluster_id = ?
        """,
        (cluster_summary, cluster_id),
    )
    conn.commit()


def update_image_event_processed(
    *,
    event_id: int,
    phash: str,
    cluster_id: int,
    ocr_text: str | None,
    summary_text: str | None,
    processed_at: datetime,
    file_deleted: bool,
) -> None:
    conn = _require_connection()
    timestamp = processed_at.replace(microsecond=0).isoformat(sep=" ")
    conn.execute(
        """
        UPDATE image_events
        SET phash = ?,
            cluster_id = ?,
            ocr_text = ?,
            summary_text = ?,
            processing_status = 'processed',
            processed_at = ?,
            file_deleted = ?,
            local_path = NULL
        WHERE id = ?
        """,
        (phash, cluster_id, ocr_text, summary_text, timestamp, int(file_deleted), event_id),
    )
    conn.commit()


def update_image_event_failed(
    *,
    event_id: int,
    processed_at: datetime,
    file_deleted: bool,
) -> None:
    conn = _require_connection()
    timestamp = processed_at.replace(microsecond=0).isoformat(sep=" ")
    conn.execute(
        """
        UPDATE image_events
        SET processing_status = 'failed',
            processed_at = ?,
            file_deleted = ?,
            local_path = NULL
        WHERE id = ?
        """,
        (timestamp, int(file_deleted), event_id),
    )
    conn.commit()


def get_image_events_by_day(day_value: date | datetime | str) -> list[dict[str, Any]]:
    conn = _require_connection()
    day = _as_iso_date(day_value)
    rows = conn.execute(
        """
        SELECT
            id, message_id, chat_id, file_id, created_at, file_size, context_text,
            phash, cluster_id, ocr_text, summary_text, processing_status,
            processed_at, file_deleted
        FROM image_events
        WHERE DATE(created_at) = DATE(?)
        ORDER BY created_at ASC
        """,
        (day,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_top_image_clusters_by_day(
    day_value: date | datetime | str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    conn = _require_connection()
    day = _as_iso_date(day_value)
    rows = conn.execute(
        """
        SELECT
            c.cluster_id,
            c.canonical_hash,
            c.cluster_summary,
            c.usage_count,
            COUNT(e.id) AS day_count,
            MAX(e.created_at) AS last_event_at
        FROM image_events e
        JOIN image_clusters c ON c.cluster_id = e.cluster_id
        WHERE DATE(e.created_at) = DATE(?)
          AND e.processing_status = 'processed'
        GROUP BY c.cluster_id
        ORDER BY day_count DESC, c.usage_count DESC
        LIMIT ?
        """,
        (day, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def get_image_event_by_message(*, chat_id: int, message_id: int) -> dict[str, Any] | None:
    conn = _require_connection()
    row = conn.execute(
        """
        SELECT
            e.id,
            e.message_id,
            e.chat_id,
            e.file_id,
            e.created_at,
            e.file_size,
            e.context_text,
            e.phash,
            e.cluster_id,
            e.ocr_text,
            e.summary_text,
            e.processing_status,
            e.processed_at,
            e.file_deleted,
            c.cluster_summary,
            c.usage_count
        FROM image_events e
        LEFT JOIN image_clusters c ON c.cluster_id = e.cluster_id
        WHERE e.chat_id = ?
          AND e.message_id = ?
        ORDER BY e.id DESC
        LIMIT 1
        """,
        (chat_id, message_id),
    ).fetchone()
    return dict(row) if row else None


def get_image_cluster_context(cluster_id: int, limit: int = 12, days: int = 62) -> list[dict[str, Any]]:
    conn = _require_connection()
    rows = conn.execute(
        """
        SELECT
            e.created_at,
            e.context_text,
            e.ocr_text,
            e.summary_text,
            m.text AS message_text,
            m.full_name
        FROM image_events e
        LEFT JOIN messages m
            ON m.chat_id = e.chat_id
           AND m.message_id = e.message_id
        WHERE e.cluster_id = ?
          AND e.processing_status = 'processed'
          AND e.created_at >= DATETIME('now', ?)
        ORDER BY e.created_at DESC
        LIMIT ?
        """,
        (cluster_id, f"-{days} days", limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _require_connection() -> sqlite3.Connection:
    if _connection is None:
        raise RuntimeError("Database is not initialized. Call init_db() first.")
    return _connection


def _ensure_column(table_name: str, column_name: str, column_type: str) -> None:
    conn = _require_connection()
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _backfill_image_event_context(limit: int = 25) -> None:
    conn = _require_connection()
    conn.execute(
        """
        UPDATE image_events
        SET context_text = (
            SELECT m.text
            FROM messages m
            WHERE m.chat_id = image_events.chat_id
              AND m.message_id = image_events.message_id
            LIMIT 1
        )
        WHERE id IN (
            SELECT e.id
            FROM image_events e
            JOIN messages m
              ON m.chat_id = e.chat_id
             AND m.message_id = e.message_id
            WHERE (e.context_text IS NULL OR e.context_text = '')
              AND m.text IS NOT NULL
              AND m.text != ''
            ORDER BY e.created_at DESC
            LIMIT ?
        )
        """
        ,
        (limit,),
    )


def _as_iso_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value
