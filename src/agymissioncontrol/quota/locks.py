"""Cross-project SQLite shared model lock registry and quota group resolution."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_STATE_SUBPATH = Path(".local") / "state" / "agymissioncontrol"


def resolve_quota_group(model_name: str) -> str:
    """Map any Antigravity model ID or group alias to its canonical shared quota pool."""
    normalized = model_name.lower().strip()
    if normalized == "claude and gpt models" or any(
        token in normalized for token in ("claude", "gpt", "sonnet", "opus", "haiku", "3p")
    ):
        return "Claude and GPT models"
    if normalized == "gemini 4 argon" or "argon" in normalized or "orchestrator" in normalized:
        return "Gemini 4 Argon"
    return "Gemini Models"


def get_registry_db_path() -> Path:
    """Return path to global shared `registry.db`, honoring `AGY_MC_STATE_DIR`."""
    override = os.environ.get("AGY_MC_STATE_DIR")
    base_dir = Path(override).expanduser() if override else Path.home() / _DEFAULT_STATE_SUBPATH
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "registry.db"


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _open_registry_connection() -> sqlite3.Connection:
    db_path = get_registry_db_path()
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_model_locks(conn: sqlite3.Connection) -> None:
    """Initialize WAL pragmas and the `model_locks` schema in `registry.db`."""
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA user_version = 1;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_locks (
            model TEXT PRIMARY KEY,
            locked_until TEXT NOT NULL,
            locked_by_pid INTEGER NOT NULL,
            reason TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def get_shared_model_lock_details(model: str) -> dict[str, Any] | None:
    """Return active lock details for a model or its shared quota group, else ``None``."""
    canonical_group = resolve_quota_group(model)
    now_utc = datetime.now(UTC)

    conn = _open_registry_connection()
    try:
        init_model_locks(conn)
        rows = conn.execute(
            """
            SELECT model, locked_until, locked_by_pid, reason, updated_at
            FROM model_locks
            WHERE model IN (?, ?)
            """,
            (canonical_group, model),
        ).fetchall()

        active_details: dict[str, Any] | None = None
        for row in rows:
            locked_until = _ensure_utc(datetime.fromisoformat(row["locked_until"]))
            if locked_until > now_utc and (
                active_details is None or locked_until > active_details["locked_until"]
            ):
                active_details = {
                    "model": row["model"],
                    "locked_until": locked_until,
                    "locked_by_pid": int(row["locked_by_pid"]),
                    "reason": str(row["reason"]),
                    "updated_at": _ensure_utc(datetime.fromisoformat(row["updated_at"])),
                }
        return active_details
    finally:
        conn.close()


def check_shared_model_lock(model: str) -> datetime | None:
    """Return lock expiry UTC datetime if `model` (or its pool) is actively locked."""
    details = get_shared_model_lock_details(model)
    if details is None:
        return None
    locked_until: datetime = details["locked_until"]
    return locked_until


def set_shared_model_lock(
    model: str,
    locked_until: datetime,
    reason: str = "QUOTA_DEPLETED",
) -> None:
    """Record or update an active lockout for `model`'s canonical shared quota group."""
    canonical_group = resolve_quota_group(model)
    locked_iso = _ensure_utc(locked_until).isoformat()
    now_iso = datetime.now(UTC).isoformat()
    pid = os.getpid()

    conn = _open_registry_connection()
    try:
        init_model_locks(conn)
        conn.execute(
            """
            INSERT INTO model_locks (model, locked_until, locked_by_pid, reason, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(model) DO UPDATE SET
                locked_until = excluded.locked_until,
                locked_by_pid = excluded.locked_by_pid,
                reason = excluded.reason,
                updated_at = excluded.updated_at
            WHERE datetime(excluded.locked_until) >= datetime(model_locks.locked_until)
               OR datetime(model_locks.locked_until) <= datetime('now')
            """,
            (canonical_group, locked_iso, pid, reason, now_iso),
        )
        conn.commit()
    finally:
        conn.close()


def clear_shared_model_lock(model: str, *, max_locked_until: datetime | None = None) -> None:
    """Remove any cached lock entry for `model` and its canonical shared quota group.

    When ``max_locked_until`` is supplied, only clears locks whose expiry is at or before
    that threshold so a waking rolling sleeper never removes a newer, longer lockout.
    """
    canonical_group = resolve_quota_group(model)
    conn = _open_registry_connection()
    try:
        init_model_locks(conn)
        if max_locked_until is None:
            conn.execute(
                "DELETE FROM model_locks WHERE model IN (?, ?)",
                (canonical_group, model),
            )
        else:
            max_iso = _ensure_utc(max_locked_until).isoformat()
            conn.execute(
                """
                DELETE FROM model_locks
                WHERE model IN (?, ?)
                  AND datetime(locked_until) <= datetime(?)
                """,
                (canonical_group, model, max_iso),
            )
        conn.commit()
    finally:
        conn.close()


def list_shared_model_locks(include_expired: bool = False) -> list[dict[str, Any]]:
    """Return active (or all, if `include_expired`) shared model locks from `registry.db`."""
    now_utc = datetime.now(UTC)
    conn = _open_registry_connection()
    try:
        init_model_locks(conn)
        rows = conn.execute(
            """
            SELECT model, locked_until, locked_by_pid, reason, updated_at
            FROM model_locks
            ORDER BY locked_until ASC, model ASC
            """
        ).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            locked_until = _ensure_utc(datetime.fromisoformat(row["locked_until"]))
            updated_at = _ensure_utc(datetime.fromisoformat(row["updated_at"]))
            remaining_seconds = max(0, int((locked_until - now_utc).total_seconds()))
            is_active = locked_until > now_utc
            if not include_expired and not is_active:
                continue
            results.append(
                {
                    "model": str(row["model"]),
                    "locked_until": locked_until,
                    "remaining_seconds": remaining_seconds,
                    "locked_by_pid": int(row["locked_by_pid"]),
                    "reason": str(row["reason"]),
                    "updated_at": updated_at,
                    "is_active": is_active,
                }
            )
        return results
    finally:
        conn.close()


def clear_all_shared_model_locks() -> int:
    """Delete all shared model lock entries in `registry.db` and return count cleared."""
    conn = _open_registry_connection()
    try:
        init_model_locks(conn)
        cursor = conn.execute("DELETE FROM model_locks")
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()
