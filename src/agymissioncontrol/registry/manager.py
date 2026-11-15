"""Atomic session CRUD, dead PID pruning, target resolution, and signal controls."""

from __future__ import annotations

import errno
import os
import signal
import sqlite3
from datetime import UTC, datetime

from agymissioncontrol.quota.locks import resolve_quota_group
from agymissioncontrol.registry.db import ensure_registry_schema, get_registry_db
from agymissioncontrol.registry.models import SessionState, SessionStatus

_KNOWN_POOL_TOKENS = (
    "claude",
    "gpt",
    "sonnet",
    "opus",
    "haiku",
    "3p",
    "argon",
    "orchestrator",
    "gemini",
)


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    utc_val = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc_val.isoformat()


def _parse_dt(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _row_to_session(row: sqlite3.Row) -> SessionState:
    return SessionState(
        pid=int(row["pid"]),
        project_name=str(row["project_name"]),
        project_dir=str(row["project_dir"]),
        label=str(row["label"] or ""),
        model=str(row["model"]),
        quota_group=str(row["quota_group"]),
        state=SessionStatus(str(row["state"])),
        sleep_started_at=_parse_dt(row["sleep_started_at"]),
        wakes_at=_parse_dt(row["wakes_at"]),
        credit_burn_usd=float(row["credit_burn_usd"] or 0.0),
        updated_at=_parse_dt(row["updated_at"]) or datetime.now(UTC),
    )


def is_pid_alive(pid: int) -> bool:
    """Return ``True`` when OS process `pid` exists (`PermissionError` counts as alive)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        raise
    return True


def register_session(session: SessionState) -> SessionState:
    """Insert or update a `gate` session row in `registry.db`."""
    now_iso = _to_iso(session.updated_at) or datetime.now(UTC).isoformat()
    conn = get_registry_db()
    try:
        ensure_registry_schema(conn)
        with conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    pid, project_name, project_dir, label, model, quota_group,
                    state, sleep_started_at, wakes_at, credit_burn_usd, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pid) DO UPDATE SET
                    project_name = excluded.project_name,
                    project_dir = excluded.project_dir,
                    label = excluded.label,
                    model = excluded.model,
                    quota_group = excluded.quota_group,
                    state = excluded.state,
                    sleep_started_at = excluded.sleep_started_at,
                    wakes_at = excluded.wakes_at,
                    credit_burn_usd = excluded.credit_burn_usd,
                    updated_at = excluded.updated_at;
                """,
                (
                    session.pid,
                    session.project_name,
                    session.project_dir,
                    session.label,
                    session.model,
                    session.quota_group,
                    str(session.state),
                    _to_iso(session.sleep_started_at),
                    _to_iso(session.wakes_at),
                    float(session.credit_burn_usd),
                    now_iso,
                ),
            )
    finally:
        conn.close()
    return session


def update_session_state(
    pid: int,
    state: SessionStatus | str,
    wakes_at: datetime | None = None,
    sleep_started_at: datetime | None = None,
    credit_burn_usd: float | None = None,
) -> bool:
    """Update lifecycle state and optional sleep/credit fields for `pid`."""
    now_iso = datetime.now(UTC).isoformat()
    state_str = str(SessionStatus(state))
    conn = get_registry_db()
    try:
        ensure_registry_schema(conn)
        with conn:
            if credit_burn_usd is None:
                cursor = conn.execute(
                    """
                    UPDATE sessions
                    SET state = ?, sleep_started_at = ?, wakes_at = ?, updated_at = ?
                    WHERE pid = ?;
                    """,
                    (state_str, _to_iso(sleep_started_at), _to_iso(wakes_at), now_iso, pid),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE sessions
                    SET state = ?, sleep_started_at = ?, wakes_at = ?,
                        credit_burn_usd = ?, updated_at = ?
                    WHERE pid = ?;
                    """,
                    (
                        state_str,
                        _to_iso(sleep_started_at),
                        _to_iso(wakes_at),
                        float(credit_burn_usd),
                        now_iso,
                        pid,
                    ),
                )
            return cursor.rowcount > 0
    finally:
        conn.close()


def unregister_session(pid: int) -> bool:
    """Remove session `pid` from `registry.db`."""
    conn = get_registry_db()
    try:
        ensure_registry_schema(conn)
        with conn:
            cursor = conn.execute("DELETE FROM sessions WHERE pid = ?;", (pid,))
            return cursor.rowcount > 0
    finally:
        conn.close()


def list_active_sessions(prune_dead: bool = True) -> list[SessionState]:
    """Return living registered sessions, atomically pruning dead OS PIDs."""
    conn = get_registry_db()
    try:
        ensure_registry_schema(conn)
        with conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC, pid ASC;"
            ).fetchall()
            alive: list[SessionState] = []
            dead_rows: list[tuple[int, str]] = []
            for row in rows:
                try:
                    pid = int(row["pid"])
                    session = _row_to_session(row)
                except ValueError, KeyError, TypeError:
                    continue
                if is_pid_alive(pid):
                    alive.append(session)
                else:
                    dead_rows.append((pid, str(row["updated_at"])))
            if prune_dead and dead_rows:
                conn.executemany(
                    "DELETE FROM sessions WHERE pid = ? AND updated_at = ?;",
                    dead_rows,
                )
            return alive
    finally:
        conn.close()


def resolve_target_pids(
    target: str | None = None,
    match_all: bool = False,
    state_filter: str | None = None,
) -> list[int]:
    """Resolve matching living session PIDs by PID, project, label, model/pool, or `--all`."""
    sessions = list_active_sessions()
    if state_filter is not None:
        normalized_filter = str(SessionStatus(state_filter))
        sessions = [s for s in sessions if str(s.state) == normalized_filter]

    if match_all:
        return [s.pid for s in sessions]

    if target is None or not target.strip():
        return []

    needle = target.strip()
    if needle.isdigit():
        target_pid = int(needle)
        return [s.pid for s in sessions if s.pid == target_pid]

    needle_lower = needle.lower()
    pool_match: str | None = None
    if any(token in needle_lower for token in _KNOWN_POOL_TOKENS):
        pool_match = resolve_quota_group(needle).lower()

    matched: list[int] = []
    for session in sessions:
        if (
            needle_lower in session.project_name.lower()
            or (session.label and needle_lower in session.label.lower())
            or needle_lower in session.model.lower()
            or needle_lower in session.quota_group.lower()
            or (pool_match is not None and session.quota_group.lower() == pool_match)
        ):
            matched.append(session.pid)
    return matched


def wake_session(pid: int) -> bool:
    """Dispatch `SIGUSR1` to wake a sleeping `gate` process immediately."""
    try:
        os.kill(pid, signal.SIGUSR1)
        return True
    except ProcessLookupError, PermissionError:
        unregister_session(pid)
        return False


def stop_session(pid: int) -> bool:
    """Dispatch `SIGTERM` for graceful teardown of a registered `gate` process."""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError, PermissionError:
        unregister_session(pid)
        return False


def wake_target(target: str | None = None, match_all: bool = False) -> list[int]:
    """Send `SIGUSR1` to matching sleeping gate sessions and return signaled PIDs."""
    pids = resolve_target_pids(
        target=target,
        match_all=match_all,
        state_filter=SessionStatus.SLEEPING,
    )
    return [pid for pid in pids if wake_session(pid)]


def stop_target(target: str | None = None, match_all: bool = False) -> list[int]:
    """Send `SIGTERM` to matching gate sessions and return signaled PIDs."""
    pids = resolve_target_pids(target=target, match_all=match_all)
    return [pid for pid in pids if stop_session(pid)]


def stop_all_sessions() -> list[int]:
    """Send `SIGTERM` to all active and sleeping registered gate sessions."""
    return stop_target(match_all=True)
