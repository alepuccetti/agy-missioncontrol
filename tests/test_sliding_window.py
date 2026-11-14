"""Deep verification unit tests for SQLite quota/token ledger and sleep math."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agymissioncontrol.quota import (
    QuotaSnapshotRecord,
    UsageTelemetry,
    calculate_sleep_duration,
    estimate_directional_allowance_pct,
    get_credit_spend,
    get_global_registry_db_path,
    get_quota_history,
    get_session_token_totals,
    init_ledger,
    record_quota_snapshots,
    record_token_transaction,
)


def test_calculate_sleep_duration_boundaries() -> None:
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    assert (
        calculate_sleep_duration(
            now + timedelta(seconds=300), safety_buffer_seconds=60, now_utc=now
        )
        == 360
    )
    assert (
        calculate_sleep_duration(now + timedelta(seconds=300), safety_buffer_seconds=0, now_utc=now)
        == 300
    )
    assert calculate_sleep_duration(now, safety_buffer_seconds=60, now_utc=now) == 0
    assert (
        calculate_sleep_duration(now - timedelta(seconds=30), safety_buffer_seconds=60, now_utc=now)
        == 0
    )

    # Naive timestamps normalized cleanly as UTC
    naive_now = datetime(2026, 11, 14, 12, 0, 0)
    naive_reset = datetime(2026, 11, 14, 12, 2, 5)
    assert calculate_sleep_duration(naive_reset, safety_buffer_seconds=15, now_utc=naive_now) == 140


def test_estimate_directional_allowance_pct() -> None:
    assert estimate_directional_allowance_pct(250_000, "claude-3-7-sonnet") == 25.0
    assert estimate_directional_allowance_pct(250_000, "gemini-2.5-pro") == 12.5
    assert estimate_directional_allowance_pct(0, "claude-3-7-sonnet") == 0.0


def test_init_ledger_sets_wal_pragmas_and_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj_dir = tmp_path / "repo"
    proj_dir.mkdir()

    init_ledger(proj_dir)
    db_path = proj_dir / ".agymissioncontrol" / "missioncontrol.db"
    assert db_path.exists()

    conn = sqlite3.connect(str(db_path))
    try:
        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        user_version = conn.execute("PRAGMA user_version;").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        }
        assert str(journal).lower() == "wal"
        assert user_version == 1
        assert {"quota_snapshots", "token_transactions"} <= tables
    finally:
        conn.close()


def test_record_and_query_quota_snapshots_chronological(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj_dir = tmp_path / "repo-a"
    proj_dir.mkdir()

    t1 = datetime(2026, 11, 14, 10, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 14, 10, 5, 0, tzinfo=UTC)
    reset = datetime(2026, 11, 14, 15, 0, 0, tzinfo=UTC)

    records = [
        QuotaSnapshotRecord(
            model_family="Claude and GPT models",
            limit_type="five_hour",
            remaining_pct=80.0,
            reset_at=reset,
            recorded_at=t1,
            raw_line="Claude | 5h | 80.0%",
        ),
        QuotaSnapshotRecord(
            model_family="Gemini Models",
            limit_type="five_hour",
            remaining_pct=95.0,
            reset_at=reset,
            recorded_at=t1,
            raw_line="Gemini | 5h | 95.0%",
        ),
        QuotaSnapshotRecord(
            model_family="Claude and GPT models",
            limit_type="five_hour",
            remaining_pct=65.0,
            reset_at=reset,
            recorded_at=t2,
            raw_line="Claude | 5h | 65.0%",
        ),
    ]
    record_quota_snapshots(records, project_dir=proj_dir)

    # Both global registry.db and project missioncontrol.db have the rows
    assert get_global_registry_db_path().exists()
    global_claude = get_quota_history(model_family="claude-3-7-sonnet", limit=10)
    assert len(global_claude) == 2
    assert [row["remaining_pct"] for row in global_claude] == [80.0, 65.0]

    proj_all = get_quota_history(project_dir=proj_dir, limit=2)
    assert len(proj_all) == 2
    assert proj_all[-1]["remaining_pct"] == 65.0


def test_token_transactions_overhead_and_credit_spend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj_dir = tmp_path / "repo-b"
    proj_dir.mkdir()

    # Empty project ledger starts cleanly at zero
    assert get_credit_spend(proj_dir) == 0.0
    assert get_session_token_totals(proj_dir).total_tokens == 0

    t_old = datetime(2026, 11, 14, 8, 0, 0, tzinfo=UTC)
    t_new = datetime(2026, 11, 14, 9, 0, 0, tzinfo=UTC)

    row1 = record_token_transaction(
        proj_dir,
        task_id="E01-T01",
        model="claude-3-7-sonnet",
        usage=UsageTelemetry(
            prompt_tokens=400_000,
            cache_read_tokens=100_000,
            candidate_tokens=50_000,
            work_tokens=450_000,
            overhead_tokens=50_000,
            is_credit_funded=False,
            credit_burn_usd=0.0,
        ),
        recorded_at=t_old,
    )
    row2 = record_token_transaction(
        proj_dir,
        task_id="E01-T02",
        model="claude-3-7-sonnet",
        usage=UsageTelemetry(
            prompt_tokens=100_000,
            cache_read_tokens=100_000,
            candidate_tokens=50_000,
            work_tokens=180_000,
            overhead_tokens=20_000,
            retry_tax_tokens=10_000,
            estimated_cost_usd=1.25,
            is_credit_funded=True,
            credit_burn_usd=1.25,
            credit_trigger_reason="WEEKLY_DEPLETED",
        ),
        recorded_at=t_new,
    )
    assert row1 == 1
    assert row2 == 2

    totals = get_session_token_totals(proj_dir)
    assert totals.work_tokens == 630_000
    assert totals.overhead_tokens == 70_000
    assert totals.total_tokens == 700_000
    assert totals.overhead_pct == 10.0
    assert totals.retry_tax_tokens == 10_000
    assert totals.is_credit_funded is True
    assert totals.credit_burn_usd == 1.25
    assert totals.estimated_cost_usd > 1.25

    assert get_credit_spend(proj_dir) == 1.25
    assert get_credit_spend(proj_dir, since=t_new) == 1.25
    assert get_credit_spend(proj_dir, since=t_new + timedelta(seconds=1)) == 0.0
    assert get_session_token_totals(proj_dir, since=t_new).total_tokens == 200_000
