"""Tests for `agymissioncontrol report`, `locks`, `unlock`, ledger isolation, and `on_event`."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agymissioncontrol.cli import app
from agymissioncontrol.config import CreditConfig, CreditPolicy, CreditTrigger, load_config
from agymissioncontrol.quota import (
    GateAction,
    MockProvider,
    QuotaSnapshotRecord,
    UsageTelemetry,
    build_quota_report,
    evaluate_and_gate,
    parse_since_arg,
    record_quota_snapshots,
    record_token_transaction,
    set_shared_model_lock,
)
from agymissioncontrol.quota.locks import get_registry_db_path, init_model_locks

runner = CliRunner()


def test_parse_since_arg_durations_and_iso_timestamps() -> None:
    ref = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)
    assert parse_since_arg(None, now=ref) is None
    assert parse_since_arg("  ", now=ref) is None
    assert parse_since_arg("30m", now=ref) == ref - timedelta(minutes=30)
    assert parse_since_arg("24h", now=ref) == ref - timedelta(hours=24)
    assert parse_since_arg("7d", now=ref) == ref - timedelta(days=7)
    assert parse_since_arg("2w", now=ref) == ref - timedelta(weeks=2)
    assert parse_since_arg("2026-11-14T10:00:00", now=ref) == datetime(
        2026, 11, 14, 10, 0, 0, tzinfo=UTC
    )
    assert parse_since_arg("2026-11-14T10:00:00+02:00", now=ref) == datetime(
        2026, 11, 14, 8, 0, 0, tzinfo=UTC
    )

    with pytest.raises(ValueError):
        parse_since_arg("0h", now=ref)
    with pytest.raises(ValueError):
        parse_since_arg("not-a-duration", now=ref)


def test_multi_project_ledger_and_report_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj_a = tmp_path / "proj_a"
    proj_b = tmp_path / "proj_b"
    proj_a.mkdir()
    proj_b.mkdir()

    now = datetime.now(UTC)
    reset_ts = now + timedelta(hours=2)

    record_quota_snapshots(
        [
            QuotaSnapshotRecord(
                model_family="Gemini Models",
                limit_type="five_hour",
                remaining_pct=75.0,
                reset_at=reset_ts,
                recorded_at=now - timedelta(hours=2),
            ),
            QuotaSnapshotRecord(
                model_family="Gemini Models",
                limit_type="five_hour",
                remaining_pct=40.0,
                reset_at=reset_ts,
                recorded_at=now - timedelta(minutes=10),
            ),
        ],
        project_dir=proj_a,
    )
    record_quota_snapshots(
        [
            QuotaSnapshotRecord(
                model_family="Claude and GPT models",
                limit_type="weekly",
                remaining_pct=12.0,
                reset_at=now + timedelta(days=2),
                recorded_at=now - timedelta(minutes=5),
            )
        ],
        project_dir=proj_b,
    )

    record_token_transaction(
        proj_a,
        task_id="T-A1",
        model="gemini-2.5-pro",
        usage=UsageTelemetry(
            prompt_tokens=1000,
            candidate_tokens=500,
            work_tokens=1200,
            overhead_tokens=300,
            cache_read_tokens=500,
            estimated_cost_usd=0.25,
            is_credit_funded=True,
            credit_burn_usd=0.25,
            credit_trigger_reason="weekly_depleted",
        ),
        recorded_at=now - timedelta(minutes=15),
    )
    record_token_transaction(
        proj_b,
        task_id="T-B1",
        model="claude-3-7-sonnet",
        usage=UsageTelemetry(
            work_tokens=5000,
            overhead_tokens=1000,
            estimated_cost_usd=4.50,
            is_credit_funded=True,
            credit_burn_usd=4.50,
            credit_trigger_reason="bypass_5h_quota",
        ),
        recorded_at=now - timedelta(minutes=5),
    )

    cfg_a = load_config(project_dir=proj_a)
    cfg_b = load_config(project_dir=proj_b)
    report_a = build_quota_report(cfg_a)
    report_b = build_quota_report(cfg_b)

    assert len(report_a.pools) == 1
    assert report_a.pools[0].model_family == "Gemini Models"
    assert report_a.pools[0].latest_remaining_pct == pytest.approx(40.0)
    assert report_a.pools[0].min_remaining_pct == pytest.approx(40.0)
    assert report_a.pools[0].max_remaining_pct == pytest.approx(75.0)
    assert report_a.credit_burn_usd == pytest.approx(0.25)
    assert report_a.usage.total_tokens == 1500
    assert report_a.credit_trigger_counts == {"weekly_depleted": 1}

    assert len(report_b.pools) == 1
    assert report_b.pools[0].model_family == "Claude and GPT models"
    assert report_b.credit_burn_usd == pytest.approx(4.50)
    assert report_b.usage.total_tokens == 6000
    assert report_b.credit_trigger_counts == {"bypass_5h_quota": 1}

    res_a = runner.invoke(
        app,
        ["report", "--project-dir", str(proj_a), "--format", "md", "--write"],
    )
    assert res_a.exit_code == 0, res_a.output
    assert "# Quota & Credit Report: proj_a" in res_a.output
    a_reports = list((proj_a / ".agymissioncontrol" / "reports").glob("quota_report_*.md"))
    assert len(a_reports) == 1
    assert "T-B1" not in a_reports[0].read_text(encoding="utf-8")
    assert not (proj_b / ".agymissioncontrol" / "reports").exists()
    assert ".agymissioncontrol/" in (proj_a / ".gitignore").read_text(encoding="utf-8")


def test_report_since_window_formats_and_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj = tmp_path / "proj"
    proj.mkdir()

    now = datetime.now(UTC)
    record_quota_snapshots(
        [
            QuotaSnapshotRecord(
                model_family="Gemini Models",
                limit_type="five_hour",
                remaining_pct=15.0,
                reset_at=now + timedelta(hours=1),
                recorded_at=now - timedelta(days=3),
            ),
            QuotaSnapshotRecord(
                model_family="Gemini Models",
                limit_type="five_hour",
                remaining_pct=88.0,
                reset_at=now + timedelta(hours=4),
                recorded_at=now - timedelta(hours=1),
            ),
        ],
        project_dir=proj,
    )
    record_token_transaction(
        proj,
        task_id="OLD",
        model="gemini-2.5-pro",
        usage=UsageTelemetry(
            work_tokens=400,
            overhead_tokens=100,
            estimated_cost_usd=2.0,
            is_credit_funded=True,
            credit_burn_usd=2.0,
        ),
        recorded_at=now - timedelta(days=3),
    )
    record_token_transaction(
        proj,
        task_id="RECENT",
        model="gemini-2.5-pro",
        usage=UsageTelemetry(
            work_tokens=200,
            overhead_tokens=50,
            estimated_cost_usd=0.5,
            is_credit_funded=True,
            credit_burn_usd=0.5,
            credit_trigger_reason="weekly_only",
        ),
        recorded_at=now - timedelta(minutes=30),
    )

    json_res = runner.invoke(
        app,
        [
            "report",
            "--project-dir",
            str(proj),
            "--since",
            "24h",
            "--format",
            "json",
            "--write",
        ],
    )
    assert json_res.exit_code == 0, json_res.output
    json_files = list((proj / ".agymissioncontrol" / "reports").glob("quota_report_*.json"))
    assert len(json_files) == 1
    parsed = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert parsed["credit_burn_usd"] == pytest.approx(0.5)
    assert parsed["usage"]["total_tokens"] == 250
    assert parsed["pools"][0]["snapshot_count"] == 1
    assert parsed["pools"][0]["latest_remaining_pct"] == pytest.approx(88.0)

    rich_res = runner.invoke(app, ["report", "--project-dir", str(proj)])
    assert rich_res.exit_code == 0, rich_res.output
    assert "Quota Pool Telemetry" in rich_res.output

    bad_res = runner.invoke(app, ["report", "--project-dir", str(proj), "--since", "bogus"])
    assert bad_res.exit_code != 0


def test_locks_and_unlock_cli_flows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))

    empty_res = runner.invoke(app, ["locks"])
    assert empty_res.exit_code == 0
    assert "No active shared model locks." in empty_res.output

    set_shared_model_lock(
        "claude-3-7-sonnet",
        datetime.now(UTC) + timedelta(minutes=20),
        reason="5H_ROLLING_EXHAUSTED",
    )
    set_shared_model_lock(
        "gemini-2.5-pro",
        datetime.now(UTC) + timedelta(days=1),
        reason="WEEKLY_LOCKOUT",
    )

    # Also inject an expired row directly to verify `--include-expired`
    conn = sqlite3.connect(str(get_registry_db_path()))
    try:
        init_model_locks(conn)
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        conn.execute(
            """
            INSERT INTO model_locks (model, locked_until, locked_by_pid, reason, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Gemini 4 Argon", past, 99999, "EXPIRED_TEST", past),
        )
        conn.commit()
    finally:
        conn.close()

    json_res = runner.invoke(app, ["locks", "--json"])
    assert json_res.exit_code == 0, json_res.output
    active = json.loads(json_res.output)
    assert {entry["model"] for entry in active} == {"Claude and GPT models", "Gemini Models"}

    all_res = runner.invoke(app, ["locks", "--json", "--include-expired"])
    assert all_res.exit_code == 0
    with_expired = json.loads(all_res.output)
    assert len(with_expired) == 3

    table_res = runner.invoke(app, ["locks"])
    assert table_res.exit_code == 0
    assert "Claude and GPT models" in table_res.output

    missing_arg_res = runner.invoke(app, ["unlock"])
    assert missing_arg_res.exit_code == 1

    unlock_one_res = runner.invoke(app, ["unlock", "gemini-2.5-flash"])
    assert unlock_one_res.exit_code == 0
    assert "Cleared shared lock for Gemini Models" in unlock_one_res.output

    remaining = json.loads(runner.invoke(app, ["locks", "--json"]).output)
    assert [entry["model"] for entry in remaining] == ["Claude and GPT models"]

    unlock_all_res = runner.invoke(app, ["unlock", "--all"])
    assert unlock_all_res.exit_code == 0
    assert "Cleared 2 shared model lock(s)." in unlock_all_res.output
    assert json.loads(runner.invoke(app, ["locks", "--json"]).output) == []


def test_governor_on_event_callbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj = tmp_path / "proj"
    proj.mkdir()
    now = datetime.now(UTC)

    # 1. SLEEP_ENTERED via provider rolling depletion
    events: list[tuple[str, dict[str, object]]] = []
    rolling_provider = MockProvider(
        models_payload={
            "Claude and GPT models": {
                "five_hour": {
                    "remaining_pct": 2.0,
                    "reset_at": (now + timedelta(seconds=30)).isoformat(),
                },
                "weekly": {
                    "remaining_pct": 80.0,
                    "reset_at": (now + timedelta(days=3)).isoformat(),
                },
            }
        },
        project_dir=proj,
    )
    decision_sleep = evaluate_and_gate(
        model="claude-3-7-sonnet",
        provider=rolling_provider,
        credit_cfg=CreditConfig(policy=CreditPolicy.NEVER),
        safety_buffer_seconds=0,
        auto_sleep=True,
        project_dir=proj,
        sleep_fn=lambda _secs: None,
        is_tty=False,
        on_event=lambda name, payload: events.append((name, payload)),
    )
    assert decision_sleep.action == GateAction.PROCEED_FREE
    assert [e[0] for e in events] == ["SLEEP_ENTERED"]
    assert events[0][1]["source"] == "provider"

    # 2. CREDIT_BUDGET_EXCEEDED + HARD_LOCKOUT via depleted credit cap
    record_token_transaction(
        proj,
        task_id="CAP-HIT",
        model="claude-3-7-sonnet",
        usage=UsageTelemetry(
            work_tokens=100,
            is_credit_funded=True,
            credit_burn_usd=5.0,
            credit_trigger_reason="weekly_only",
        ),
    )
    budget_events: list[tuple[str, dict[str, object]]] = []
    weekly_provider = MockProvider(
        models_payload={
            "Claude and GPT models": {
                "five_hour": {
                    "remaining_pct": 90.0,
                    "reset_at": (now + timedelta(hours=3)).isoformat(),
                },
                "weekly": {
                    "remaining_pct": 1.0,
                    "reset_at": (now + timedelta(hours=4)).isoformat(),
                },
            }
        },
        project_dir=proj,
    )
    decision_cap = evaluate_and_gate(
        model="claude-3-7-sonnet",
        provider=weekly_provider,
        credit_cfg=CreditConfig(
            policy=CreditPolicy.BUDGET_CAPPED,
            max_burn_usd=5.0,
            trigger=CreditTrigger.WEEKLY_ONLY,
        ),
        project_dir=proj,
        on_event=lambda name, payload: budget_events.append((name, payload)),
    )
    assert decision_cap.action == GateAction.LOCKOUT_CREDIT_BUDGET
    assert [e[0] for e in budget_events] == ["CREDIT_BUDGET_EXCEEDED", "HARD_LOCKOUT"]

    # 3. HARD_LOCKOUT via shared weekly lock (with policy=NEVER)
    shared_events: list[tuple[str, dict[str, object]]] = []
    decision_shared = evaluate_and_gate(
        model="claude-3-7-sonnet",
        provider=weekly_provider,
        credit_cfg=CreditConfig(policy=CreditPolicy.NEVER),
        project_dir=proj,
        on_event=lambda name, payload: shared_events.append((name, payload)),
    )
    assert decision_shared.action == GateAction.LOCKOUT_WEEKLY
    assert [e[0] for e in shared_events] == ["HARD_LOCKOUT"]
    assert shared_events[0][1]["source"] == "shared_lock"
