"""Tests for E04-T02 gate session registration, `SIGUSR1` wakeup loop, `ps`, `wake`, and `stop`."""

from __future__ import annotations

import json
import os
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agymissioncontrol.cli import app
from agymissioncontrol.quota import governor
from agymissioncontrol.quota.locks import check_shared_model_lock, set_shared_model_lock
from agymissioncontrol.quota.models import ModelQuota
from agymissioncontrol.registry import (
    SessionState,
    SessionStatus,
    list_active_sessions,
    register_session,
)

runner = CliRunner()


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate `AGY_MC_STATE_DIR` and return a temporary project directory."""
    state_dir = tmp_path / "state"
    project_dir = tmp_path / "demo-project"
    project_dir.mkdir(parents=True)
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(state_dir))
    return project_dir


def test_ps_rich_and_json_and_pruning_dead_pids(isolated_env: Path) -> None:
    """`ps` renders Rich and JSON outputs for living sessions while pruning dead PIDs."""
    empty_res = runner.invoke(app, ["ps"])
    assert empty_res.exit_code == 0
    assert "No active or sleeping gate sessions." in empty_res.stdout

    alive_pid = os.getpid()
    dead_pid = 999991
    register_session(
        SessionState(
            pid=alive_pid,
            project_name="demo-project",
            project_dir=str(isolated_env),
            label="e04-t02",
            model="claude-sonnet-4-6",
            quota_group="Claude and GPT models",
            state=SessionStatus.SLEEPING,
            sleep_started_at=datetime.now(UTC),
            wakes_at=datetime.now(UTC) + timedelta(minutes=10),
            credit_burn_usd=0.75,
        )
    )
    register_session(
        SessionState(
            pid=dead_pid,
            project_name="dead-project",
            project_dir="/tmp/dead-project",
            label="stale",
            model="gemini-3-pro",
            quota_group="Gemini Models",
            state=SessionStatus.ACTIVE,
        )
    )

    def fake_kill(pid: int, sig: int) -> None:
        if sig == 0 and pid == dead_pid:
            raise ProcessLookupError(3, "No such process")

    with patch("agymissioncontrol.registry.manager.os.kill", side_effect=fake_kill):
        rich_res = runner.invoke(app, ["ps"])
        assert rich_res.exit_code == 0
        assert str(alive_pid) in rich_res.stdout
        assert "demo-project" in rich_res.stdout
        assert "e04-t02" in rich_res.stdout
        assert "SLEEPING" in rich_res.stdout
        assert "$0.75" in rich_res.stdout
        assert "dead-project" not in rich_res.stdout

        json_res = runner.invoke(app, ["ps", "--json"])
        assert json_res.exit_code == 0
        payload = json.loads(json_res.stdout)
        assert len(payload) == 1
        assert payload[0]["pid"] == alive_pid
        assert payload[0]["label"] == "e04-t02"


def test_wake_and_stop_cli_dispatch_signals_and_errors(isolated_env: Path) -> None:
    """`wake` and `stop` dispatch SIGUSR1 / SIGTERM by target filters or `--all`."""
    register_session(
        SessionState(
            pid=61001,
            project_name="svc-alpha",
            project_dir=str(isolated_env),
            label="auth-flow",
            model="claude-sonnet-4-6",
            quota_group="Claude and GPT models",
            state=SessionStatus.SLEEPING,
            wakes_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    register_session(
        SessionState(
            pid=61002,
            project_name="svc-beta",
            project_dir=str(isolated_env),
            label="ui-build",
            model="gemini-pro-orchestrator-g1-pro",
            quota_group="Gemini 4 Argon",
            state=SessionStatus.ACTIVE,
        )
    )

    # Missing argument and --all fails cleanly.
    assert runner.invoke(app, ["wake"]).exit_code == 1
    assert runner.invoke(app, ["stop"]).exit_code == 1

    sent: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        if sig == 0:
            return
        sent.append((pid, sig))

    with patch("agymissioncontrol.registry.manager.os.kill", side_effect=fake_kill):
        wake_res = runner.invoke(app, ["wake", "auth-flow"])
        assert wake_res.exit_code == 0
        assert "61001" in wake_res.stdout

        # Active session is not eligible for wake.
        assert runner.invoke(app, ["wake", "svc-beta"]).exit_code == 1

        stop_res = runner.invoke(app, ["stop", "61002"])
        assert stop_res.exit_code == 0
        assert "61002" in stop_res.stdout

        stop_all_res = runner.invoke(app, ["stop", "--all"])
        assert stop_all_res.exit_code == 0
        assert "61001" in stop_all_res.stdout and "61002" in stop_all_res.stdout

    assert (61001, signal.SIGUSR1) in sent
    assert (61002, signal.SIGTERM) in sent
    assert (61001, signal.SIGTERM) in sent


def test_gate_label_registers_transitions_on_sigusr1_and_unregisters(
    isolated_env: Path,
) -> None:
    """`gate --label` registers, transitions SLEEPING->ACTIVE on SIGUSR1 wake, and unregisters."""
    reset_at = datetime.now(UTC) + timedelta(minutes=10)
    calls = {"quota": 0, "sleep": 0}
    seen_states_during_sleep: list[SessionStatus] = []

    class _WakeReevalProvider:
        def get_model_quota(
            self,
            model_name: str,
            rolling_threshold: float = 15.0,
            weekly_threshold: float = 10.0,
            respect_shared_lock: bool = True,
        ) -> ModelQuota:
            calls["quota"] += 1
            if calls["quota"] == 1:
                return ModelQuota(
                    model_name=model_name,
                    quota_group="Claude and GPT models",
                    rolling_window_remaining_pct=2.0,
                    rolling_reset_epoch=reset_at,
                    weekly_remaining_pct=90.0,
                )
            return ModelQuota(
                model_name=model_name,
                quota_group="Claude and GPT models",
                rolling_window_remaining_pct=95.0,
                rolling_reset_epoch=reset_at,
                weekly_remaining_pct=90.0,
            )

    orig_execute_sleep = governor.execute_sleep

    def real_execute_sleep_with_sigusr1(*args: Any, **kwargs: Any) -> bool:
        calls["sleep"] += 1
        active_rows = list_active_sessions()
        assert len(active_rows) == 1
        assert active_rows[0].label == "orchestrator-task"
        seen_states_during_sleep.append(active_rows[0].state)
        assert check_shared_model_lock("claude-sonnet-4-6") is not None

        def _send_sigusr1(_seconds: float) -> None:
            os.kill(os.getpid(), signal.SIGUSR1)

        sleep_kwargs = dict(kwargs)
        sleep_kwargs["sleep_fn"] = _send_sigusr1
        sleep_kwargs["is_tty"] = False
        return orig_execute_sleep(*args, **sleep_kwargs)

    with (
        patch("agymissioncontrol.cli._build_provider", return_value=_WakeReevalProvider()),
        patch(
            "agymissioncontrol.quota.governor.execute_sleep",
            side_effect=real_execute_sleep_with_sigusr1,
        ),
    ):
        res = runner.invoke(
            app,
            [
                "gate",
                "--model",
                "claude-sonnet-4-6",
                "--label",
                "orchestrator-task",
                "--project-dir",
                str(isolated_env),
            ],
        )

    assert res.exit_code == 0
    assert "GO (FREE):" in res.stdout
    assert calls == {"quota": 2, "sleep": 1}
    assert seen_states_during_sleep == [SessionStatus.SLEEPING]
    assert check_shared_model_lock("claude-sonnet-4-6") is None
    assert list_active_sessions() == []


def test_gate_max_sleep_exceeded_exits_1_and_unregisters(isolated_env: Path) -> None:
    """When `--max-sleep` is exceeded, `gate` exits 1 and unregisters cleanly."""
    set_shared_model_lock(
        model="claude-sonnet-4-6",
        locked_until=datetime.now(UTC) + timedelta(minutes=20),
        reason="5H_ROLLING_EXHAUSTED",
    )
    res = runner.invoke(
        app,
        [
            "gate",
            "--model",
            "claude-sonnet-4-6",
            "--label",
            "bounded-wait",
            "--max-sleep",
            "30",
            "--project-dir",
            str(isolated_env),
            "--mock",
        ],
    )
    assert res.exit_code == 1
    assert "LOW_QUOTA:" in res.stdout
    assert list_active_sessions() == []
