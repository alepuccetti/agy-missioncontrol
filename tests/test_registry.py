"""Tests for E04-T01 SQLite gate process & quota registry (`registry.db`)."""

from __future__ import annotations

import os
import signal
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from agymissioncontrol.quota.ledger import record_quota_snapshots
from agymissioncontrol.quota.locks import (
    get_shared_model_lock_details,
    set_shared_model_lock,
)
from agymissioncontrol.quota.models import QuotaSnapshotRecord
from agymissioncontrol.registry import (
    SessionState,
    SessionStatus,
    get_registry_db,
    get_state_dir,
    init_registry,
    list_active_sessions,
    register_session,
    resolve_target_pids,
    stop_all_sessions,
    stop_session,
    stop_target,
    unregister_session,
    update_session_state,
    wake_session,
    wake_target,
)


@pytest.fixture
def isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point `AGY_MC_STATE_DIR` to an isolated temporary directory."""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(state_dir))
    return state_dir


def test_registry_db_pragmas_and_unified_e01_schema(isolated_registry: Path) -> None:
    """`registry.db` enables WAL, 30s busy timeout, user_version=1, and E01 schemas."""
    assert get_state_dir() == isolated_registry

    init_registry()
    conn = get_registry_db()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        user_version = conn.execute("PRAGMA user_version;").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        }
        lock_cols = {row[1] for row in conn.execute("PRAGMA table_info(model_locks);").fetchall()}
    finally:
        conn.close()

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) == 30000
    assert int(user_version) == 1
    assert {"sessions", "model_locks", "quota_snapshots"}.issubset(tables)
    assert "locked_by_pid" in lock_cols

    # Verify seamless interoperability with E01 locks.py and ledger.py helpers.
    set_shared_model_lock(
        model="claude-sonnet-4-6",
        locked_until=datetime.now(UTC) + timedelta(minutes=30),
        reason="5h rolling limit",
    )
    lock_details = get_shared_model_lock_details("claude-sonnet-4-6")
    assert lock_details is not None
    assert lock_details["locked_by_pid"] == os.getpid()

    record_quota_snapshots(
        [
            QuotaSnapshotRecord(
                recorded_at=datetime.now(UTC),
                model_family="Gemini 4 Argon",
                limit_type="weekly",
                remaining_pct=82.5,
                reset_at=datetime.now(UTC) + timedelta(days=3),
            )
        ],
        project_dir=None,
    )


def test_session_state_remaining_sleep_seconds_computed_field() -> None:
    """`remaining_sleep_seconds` handles sleeping, non-sleeping, naive UTC, and past times."""
    future_naive = (datetime.now(UTC) + timedelta(seconds=125)).replace(tzinfo=None)
    sleeping = SessionState(
        pid=101,
        project_name="alpha",
        project_dir="/tmp/alpha",
        model="claude-sonnet-4-6",
        quota_group="Claude and GPT models",
        state=SessionStatus.SLEEPING,
        wakes_at=future_naive,
    )
    assert sleeping.remaining_sleep_seconds is not None
    assert 115 <= sleeping.remaining_sleep_seconds <= 125

    active = sleeping.model_copy(update={"state": SessionStatus.ACTIVE})
    assert active.remaining_sleep_seconds is None

    expired = sleeping.model_copy(update={"wakes_at": datetime.now(UTC) - timedelta(seconds=10)})
    assert expired.remaining_sleep_seconds == 0


def test_session_lifecycle_and_atomic_dead_pid_pruning(isolated_registry: Path) -> None:
    """Living PIDs (incl. EPERM) are returned; dead PIDs (ESRCH) are pruned atomically."""
    alive_pid = 42001
    perm_pid = 42002
    dead_pid = 42003

    for pid, proj, label in (
        (alive_pid, "repo-a", "backend"),
        (perm_pid, "repo-b", "frontend"),
        (dead_pid, "repo-c", "stale-worker"),
    ):
        register_session(
            SessionState(
                pid=pid,
                project_name=proj,
                project_dir=f"/tmp/{proj}",
                label=label,
                model="claude-sonnet-4-6",
                quota_group="Claude and GPT models",
                state=SessionStatus.ACTIVE,
            )
        )

    wake_time = datetime.now(UTC) + timedelta(minutes=15)
    assert update_session_state(
        alive_pid,
        SessionStatus.SLEEPING,
        wakes_at=wake_time,
        sleep_started_at=datetime.now(UTC),
        credit_burn_usd=1.25,
    )

    def fake_kill(pid: int, sig: int) -> None:
        if sig == 0:
            if pid == dead_pid:
                raise ProcessLookupError(3, "No such process")
            if pid == perm_pid:
                raise PermissionError(1, "Operation not permitted")
            return
        raise AssertionError(f"Unexpected signal {sig}")

    with patch("agymissioncontrol.registry.manager.os.kill", side_effect=fake_kill):
        sessions = list_active_sessions()

    returned_pids = {s.pid for s in sessions}
    assert returned_pids == {alive_pid, perm_pid}

    sleeping_row = next(s for s in sessions if s.pid == alive_pid)
    assert sleeping_row.state == SessionStatus.SLEEPING
    assert sleeping_row.credit_burn_usd == pytest.approx(1.25)

    # Confirm dead PID was physically deleted from SQLite.
    conn = get_registry_db()
    try:
        db_pids = {row[0] for row in conn.execute("SELECT pid FROM sessions;").fetchall()}
    finally:
        conn.close()
    assert db_pids == {alive_pid, perm_pid}

    assert unregister_session(perm_pid) is True
    assert unregister_session(perm_pid) is False


def test_target_resolution_and_signal_dispatch(isolated_registry: Path) -> None:
    """Target resolution and wake/stop helpers match filters and dispatch signals."""
    s1 = SessionState(
        pid=51001,
        project_name="payments-api",
        project_dir="/tmp/payments-api",
        label="e04-t01",
        model="claude-sonnet-4-6",
        quota_group="Claude and GPT models",
        state=SessionStatus.SLEEPING,
        wakes_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    s2 = SessionState(
        pid=51002,
        project_name="cockpit-ui",
        project_dir="/tmp/cockpit-ui",
        label="e04-t03",
        model="gemini-pro-orchestrator-g1-pro",
        quota_group="Gemini 4 Argon",
        state=SessionStatus.ACTIVE,
    )
    register_session(s1)
    register_session(s2)

    sent_signals: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        if sig == 0:
            return
        sent_signals.append((pid, sig))

    with patch("agymissioncontrol.registry.manager.os.kill", side_effect=fake_kill):
        assert resolve_target_pids("51001") == [51001]
        assert resolve_target_pids("payments") == [51001]
        assert resolve_target_pids("e04-t03") == [51002]
        assert resolve_target_pids("argon") == [51002]
        assert resolve_target_pids("nonexistent-target") == []
        assert set(resolve_target_pids(match_all=True)) == {51001, 51002}

        assert wake_target("claude") == [51001]
        assert wake_target("cockpit-ui") == []  # ACTIVE session is not SLEEPING
        assert wake_session(51001) is True
        assert stop_target("e04-t03") == [51002]
        assert stop_session(51002) is True
        assert set(stop_all_sessions()) == {51001, 51002}

    assert (51001, signal.SIGUSR1) in sent_signals
    assert (51002, signal.SIGTERM) in sent_signals
    assert (51001, signal.SIGTERM) in sent_signals


def test_concurrent_threaded_registrations_no_lock_errors(isolated_registry: Path) -> None:
    """Multiple concurrent threads register and update sessions without SQLite lock errors."""
    init_registry()
    current_pid = os.getpid()

    def worker(idx: int) -> None:
        pid = current_pid + idx + 10000
        register_session(
            SessionState(
                pid=pid,
                project_name=f"proj-{idx}",
                project_dir=f"/tmp/proj-{idx}",
                label=f"worker-{idx}",
                model="gemini-3-pro",
                quota_group="Gemini Models",
                state=SessionStatus.ACTIVE,
            )
        )
        update_session_state(
            pid,
            SessionStatus.SLEEPING,
            wakes_at=datetime.now(UTC) + timedelta(minutes=5),
            credit_burn_usd=0.1 * idx,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(16)))

    conn = get_registry_db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM sessions;").fetchone()[0]
    finally:
        conn.close()
    assert count == 16
