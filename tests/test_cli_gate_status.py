"""CLI integration tests for `agymissioncontrol status` and `agymissioncontrol gate`."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from agymissioncontrol.cli import app
from agymissioncontrol.quota import governor, set_shared_model_lock

runner = CliRunner()


def _official_groups_payload(
    claude_5h: float = 0.82,
    claude_weekly: float = 0.64,
    gemini_5h: float = 0.96,
    gemini_weekly: float = 0.88,
) -> dict[str, object]:
    now = datetime.now(UTC)
    reset_5h = (now + timedelta(minutes=42)).isoformat()
    reset_weekly = (now + timedelta(days=3)).isoformat()
    return {
        "command": {
            "data": {
                "groups": [
                    {
                        "name": "Claude and GPT models",
                        "buckets": [
                            {
                                "name": "5-Hour Limit",
                                "window": "5h",
                                "remaining_fraction": claude_5h,
                                "reset_time": reset_5h,
                            },
                            {
                                "name": "Weekly Limit",
                                "window": "weekly",
                                "remaining_fraction": claude_weekly,
                                "reset_time": reset_weekly,
                            },
                        ],
                    },
                    {
                        "name": "Gemini Models",
                        "buckets": [
                            {
                                "name": "5-Hour Limit",
                                "window": "5h",
                                "remaining_fraction": gemini_5h,
                                "reset_time": reset_5h,
                            },
                            {
                                "name": "Weekly Limit",
                                "window": "weekly",
                                "remaining_fraction": gemini_weekly,
                                "reset_time": reset_weekly,
                            },
                        ],
                    },
                ]
            }
        }
    }


def test_cli_status_rich_table_and_json_with_mock_and_agy_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()

    # 1. Mock table output
    res_mock = runner.invoke(
        app,
        ["status", "--mock", "--project-dir", str(proj_dir)],
        env={"COLUMNS": "160"},
    )
    assert res_mock.exit_code == 0
    assert "Model Pool" in res_mock.output
    assert "Claude and GPT models" in res_mock.output
    assert "HEALTHY" in res_mock.output

    # 2. Mock JSON output
    res_json = runner.invoke(app, ["status", "--mock", "--json", "--project-dir", str(proj_dir)])
    assert res_json.exit_code == 0
    parsed = json.loads(res_json.output)
    assert parsed["credits"]["policy"] == "never"
    assert "Claude and GPT models" in parsed["models"]
    assert parsed["models"]["Claude and GPT models"]["status"] == "HEALTHY"

    # 3. Live provider path persists snapshots to project missioncontrol.db
    payload = _official_groups_payload()

    def fake_subprocess_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    res_live = runner.invoke(app, ["status", "--json", "--project-dir", str(proj_dir)])
    assert res_live.exit_code == 0
    live_data = json.loads(res_live.output)
    assert live_data["models"]["Claude and GPT models"]["rolling_window_remaining_pct"] == 82.0

    conn = sqlite3.connect(str(proj_dir / ".agymissioncontrol" / "missioncontrol.db"))
    try:
        rows = conn.execute("SELECT COUNT(*) FROM quota_snapshots").fetchone()[0]
        assert rows == 4
    finally:
        conn.close()


def test_cli_gate_healthy_rolling_weekly_and_credit_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj_dir = tmp_path / "proj-gate"
    proj_dir.mkdir()

    # Healthy quota -> exit 0 (GO (FREE))
    healthy_payload = _official_groups_payload(claude_5h=0.80, claude_weekly=0.70)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps(healthy_payload), stderr="", returncode=0
        ),
    )
    res_ok = runner.invoke(
        app,
        ["gate", "--model", "claude-3-7-sonnet", "--project-dir", str(proj_dir)],
    )
    assert res_ok.exit_code == 0
    assert "GO (FREE)" in res_ok.output

    # 5h depleted (weekly healthy), no-auto-sleep -> exit 1 (LOW_QUOTA)
    low_5h_payload = _official_groups_payload(claude_5h=0.08, claude_weekly=0.70)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps(low_5h_payload), stderr="", returncode=0
        ),
    )
    res_low = runner.invoke(
        app,
        [
            "gate",
            "--model",
            "claude-3-7-sonnet",
            "--no-auto-sleep",
            "--project-dir",
            str(proj_dir),
        ],
    )
    assert res_low.exit_code == 1
    assert "LOW_QUOTA" in res_low.output

    # Same depleted 5h + --bypass-5h-quota + budget_capped -> exit 0 (GO (CREDIT))
    res_bypass = runner.invoke(
        app,
        [
            "gate",
            "--model",
            "claude-3-7-sonnet",
            "--credit-policy",
            "budget_capped",
            "--max-credit-burn",
            "10.0",
            "--bypass-5h-quota",
            "--project-dir",
            str(proj_dir),
        ],
    )
    assert res_bypass.exit_code == 0
    assert "GO (CREDIT)" in res_bypass.output


def test_cli_gate_weekly_lockout_exit_2_and_shared_lock_interception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj_dir = tmp_path / "proj-lock"
    proj_dir.mkdir()

    # Weekly quota at 5% < --weekly-threshold 10 with default policy=never -> exit 2 (HARD_LOCKOUT)
    weekly_low_payload = _official_groups_payload(claude_5h=0.50, claude_weekly=0.05)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps(weekly_low_payload), stderr="", returncode=0
        ),
    )
    res_weekly = runner.invoke(
        app,
        [
            "gate",
            "--model",
            "claude-3-7-sonnet",
            "--weekly-threshold",
            "10",
            "--project-dir",
            str(proj_dir),
        ],
    )
    assert res_weekly.exit_code == 2
    assert "HARD_LOCKOUT" in res_weekly.output

    # Shared lock set by another repo intercepts without calling external subprocess
    set_shared_model_lock(
        "gemini-2.5-pro",
        datetime.now(UTC) + timedelta(minutes=15),
        reason="5H_ROLLING_EXHAUSTED",
    )

    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("External `agy` subprocess must NOT be called when shared lock exists")

    monkeypatch.setattr(subprocess, "run", boom)
    res_locked = runner.invoke(
        app,
        [
            "gate",
            "--model",
            "gemini-2.5-pro",
            "--no-auto-sleep",
            "--project-dir",
            str(proj_dir),
        ],
    )
    assert res_locked.exit_code == 1
    assert "LOW_QUOTA" in res_locked.output

    # With auto-sleep enabled, enters execute_sleep cleanly without external telemetry calls
    sleep_calls: list[str] = []
    monkeypatch.setattr(
        governor,
        "execute_sleep",
        lambda seconds, model_name, **kw: sleep_calls.append(model_name) or True,
    )
    res_woke = runner.invoke(
        app,
        ["gate", "--model", "gemini-2.5-pro", "--auto-sleep", "--project-dir", str(proj_dir)],
    )
    assert res_woke.exit_code == 0
    assert sleep_calls == ["gemini-2.5-pro"]
    assert "GO (FREE)" in res_woke.output
