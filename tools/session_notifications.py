"""Durable cross-process notifications for an existing local chat session."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from typing import Any

from hermes_constants import get_hermes_home

_INIT_LOCK = threading.Lock()
_INITIALIZED_PATHS: set[str] = set()


def _connect() -> sqlite3.Connection:
    path = get_hermes_home() / "state.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    path_key = str(path.resolve())
    if path_key not in _INITIALIZED_PATHS:
        with _INIT_LOCK:
            if path_key not in _INITIALIZED_PATHS:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS session_notifications (
                        notification_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        message TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        delivered_at REAL,
                        delivery_claim TEXT,
                        delivery_claimed_at REAL
                    )"""
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_session_notifications_pending "
                    "ON session_notifications(session_id, delivered_at, created_at)"
                )
                conn.commit()
                _INITIALIZED_PATHS.add(path_key)
    return conn


def enqueue_notification(
    *,
    session_id: str,
    message: str,
    notification_id: str | None = None,
    kind: str = "system",
) -> str:
    """Persist one idempotent notification for a local session."""
    notification_id = notification_id or uuid.uuid4().hex
    with _connect() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO session_notifications
               (notification_id, session_id, kind, message, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (notification_id, str(session_id), str(kind), str(message), time.time()),
        )
    return notification_id


def claim_notifications(
    *,
    session_id: str,
    consumer: str,
    limit: int = 20,
    claim_ttl: float = 300.0,
) -> tuple[str, list[dict[str, Any]]]:
    """Claim pending rows for one session without exposing other conversations."""
    claim_id = f"{consumer}:{__import__('os').getpid()}:{uuid.uuid4().hex}"
    now = time.time()
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT notification_id, session_id, kind, message, created_at
                 FROM session_notifications
                WHERE session_id = ? AND delivered_at IS NULL
                  AND (delivery_claim IS NULL OR delivery_claimed_at < ?)
                ORDER BY created_at, notification_id
                LIMIT ?""",
            (str(session_id), now - claim_ttl, max(1, int(limit))),
        ).fetchall()
        ids = [row["notification_id"] for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE session_notifications SET delivery_claim = ?, "
                f"delivery_claimed_at = ? WHERE notification_id IN ({placeholders})",
                (claim_id, now, *ids),
            )
        conn.commit()
        return claim_id, [dict(row) for row in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def acknowledge_notifications(notification_ids: list[str], claim_id: str) -> int:
    """Mark claimed notifications delivered after the CLI accepted them."""
    if not notification_ids:
        return 0
    placeholders = ",".join("?" for _ in notification_ids)
    with _connect() as conn:
        cur = conn.execute(
            f"""UPDATE session_notifications
                   SET delivered_at = ?, delivery_claim = NULL,
                       delivery_claimed_at = NULL
                 WHERE notification_id IN ({placeholders})
                   AND delivery_claim = ? AND delivered_at IS NULL""",
            (time.time(), *notification_ids, claim_id),
        )
    return cur.rowcount


def deliver_notifications_to_queue(
    *, session_id: str, target_queue, consumer: str = "cli-idle",
) -> int:
    """Claim this session's notifications and append them as fresh turns."""
    claim_id, items = claim_notifications(
        session_id=session_id,
        consumer=consumer,
    )
    accepted: list[str] = []
    for item in items:
        target_queue.put(item["message"])
        accepted.append(item["notification_id"])
    acknowledge_notifications(accepted, claim_id)
    return len(accepted)
