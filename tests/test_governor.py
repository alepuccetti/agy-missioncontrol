"""Deep verification unit tests for governor decisions, zero-token sleep, and signals."""

from __future__ import annotations

import io
import os
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agymissioncontrol.config import CreditConfig, CreditPolicy, CreditTrigger
from agymissioncontrol.quota import (
    GateAction,
    MockProvider,
    UsageTelemetry,
    check_shared_model_lock,
    evaluate_and_gate,
    evaluate_gate_decision,
    execute_sleep,
    record_token_transaction,
    set_shared_model_lock,
)


def test_evaluate_gate_decision_all_matrix_branches() -> None:
    # 1. Both quotas healthy
    action, _ = evaluate_gate_decision(
        80.0, 15.0, 70.0, 10.0, CreditConfig(), current_credit_burn_usd=0.0
    )
    assert action == GateAction.PROCEED_FREE

    # 2. Weekly depleted with different credit policies
    action, _ = evaluate_gate_decision(
        50.0,
        15.0,
        5.0,
        10.0,
        CreditConfig(policy=CreditPolicy.NEVER),
        current_credit_burn_usd=0.0,
    )
    assert action == GateAction.LOCKOUT_WEEKLY

    action, _ = evaluate_gate_decision(
        50.0,
        15.0,
        5.0,
        10.0,
        CreditConfig(policy=CreditPolicy.BUDGET_CAPPED, max_burn_usd=10.0),
        current_credit_burn_usd=4.50,
    )
    assert action == GateAction.PROCEED_CREDIT

    action, _ = evaluate_gate_decision(
        50.0,
        15.0,
        5.0,
        10.0,
        CreditConfig(policy=CreditPolicy.BUDGET_CAPPED, max_burn_usd=10.0),
        current_credit_burn_usd=10.0,
    )
    assert action == GateAction.LOCKOUT_CREDIT_BUDGET

    action, _ = evaluate_gate_decision(
        50.0,
        15.0,
        0.0,
        10.0,
        CreditConfig(policy=CreditPolicy.ALWAYS),
        current_credit_burn_usd=999.0,
    )
    assert action == GateAction.PROCEED_CREDIT

    # 3. 5h rolling depleted (weekly healthy): respects trigger & caller bypass
    action, _ = evaluate_gate_decision(
        5.0,
        15.0,
        60.0,
        10.0,
        CreditConfig(
            policy=CreditPolicy.BUDGET_CAPPED, max_burn_usd=10.0, trigger=CreditTrigger.WEEKLY_ONLY
        ),
        current_credit_burn_usd=0.0,
        caller_bypass_5h=False,
    )
    assert action == GateAction.SLEEP_ROLLING

    action, _ = evaluate_gate_decision(
        5.0,
        15.0,
        60.0,
        10.0,
        CreditConfig(
            policy=CreditPolicy.BUDGET_CAPPED, max_burn_usd=10.0, trigger=CreditTrigger.ANY
        ),
        current_credit_burn_usd=0.0,
    )
    assert action == GateAction.PROCEED_CREDIT

    action, _ = evaluate_gate_decision(
        5.0,
        15.0,
        60.0,
        10.0,
        CreditConfig(
            policy=CreditPolicy.BUDGET_CAPPED, max_burn_usd=10.0, trigger=CreditTrigger.WEEKLY_ONLY
        ),
        current_credit_burn_usd=0.0,
        caller_bypass_5h=True,
    )
    assert action == GateAction.PROCEED_CREDIT

    # 4. 5h bypass requested, but credit budget already exhausted -> sleep
    action, _ = evaluate_gate_decision(
        5.0,
        15.0,
        60.0,
        10.0,
        CreditConfig(
            policy=CreditPolicy.BUDGET_CAPPED, max_burn_usd=10.0, trigger=CreditTrigger.ANY
        ),
        current_credit_burn_usd=10.0,
    )
    assert action == GateAction.SLEEP_ROLLING


def test_execute_sleep_non_tty_heartbeats_zero_ansi() -> None:
    stream = io.StringIO()
    sleeps: list[float] = []
    target = datetime(2026, 11, 14, 15, 0, 0, tzinfo=UTC)

    completed = execute_sleep(
        seconds=125,
        model_name="claude-3-7-sonnet",
        wakes_at=target,
        sleep_fn=sleeps.append,
        is_tty=False,
        out_stream=stream,
    )
    output = stream.getvalue()
    assert completed is True
    assert len(sleeps) == 125
    assert "\x1b[" not in output
    assert "[SLEEP] Model claude-3-7-sonnet quota exhausted. Sleeping 125s until" in output
    assert output.count("[SLEEP HEARTBEAT]") == 2
    assert "[SLEEP WAKE] Quota window reset for claude-3-7-sonnet" in output

    # Zero-second sleep is a no-op
    assert execute_sleep(0, "claude-3-7-sonnet", sleep_fn=sleeps.append, is_tty=False) is True
    assert len(sleeps) == 125


def test_execute_sleep_tty_mode_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    stream = io.StringIO()
    sleeps: list[float] = []
    completed = execute_sleep(
        seconds=3,
        model_name="gemini-2.5-pro",
        sleep_fn=sleeps.append,
        is_tty=True,
        out_stream=stream,
    )
    assert completed is True
    assert len(sleeps) == 3
    assert "Quota window reset for gemini-2.5-pro" in stream.getvalue()


def test_execute_sleep_sigint_exits_130_and_restores_handlers() -> None:
    stream = io.StringIO()
    orig_sigint = signal.getsignal(signal.SIGINT)
    orig_sigterm = signal.getsignal(signal.SIGTERM)

    def trigger_sigint(_seconds: float) -> None:
        os.kill(os.getpid(), signal.SIGINT)

    with pytest.raises(SystemExit) as exc_info:
        execute_sleep(
            seconds=10,
            model_name="claude-3-7-sonnet",
            sleep_fn=trigger_sigint,
            is_tty=False,
            out_stream=stream,
        )

    assert exc_info.value.code == 130
    assert "SLEEP CANCELLED" in stream.getvalue()
    assert signal.getsignal(signal.SIGINT) is orig_sigint
    assert signal.getsignal(signal.SIGTERM) is orig_sigterm


def test_execute_sleep_sigusr1_wakes_early() -> None:
    if not hasattr(signal, "SIGUSR1"):
        pytest.skip("SIGUSR1 not supported on this platform")

    stream = io.StringIO()
    orig_sigusr1 = signal.getsignal(signal.SIGUSR1)
    ticks = {"count": 0}

    def trigger_wake_on_tick_2(_seconds: float) -> None:
        ticks["count"] += 1
        if ticks["count"] == 2:
            os.kill(os.getpid(), signal.SIGUSR1)

    completed = execute_sleep(
        seconds=20,
        model_name="claude-3-7-sonnet",
        sleep_fn=trigger_wake_on_tick_2,
        is_tty=False,
        out_stream=stream,
    )
    assert completed is False
    assert ticks["count"] == 2
    assert "Early wake signal received" in stream.getvalue()
    assert signal.getsignal(signal.SIGUSR1) is orig_sigusr1


def test_evaluate_and_gate_shared_locks_and_sleep_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj_dir = tmp_path / "repo"
    proj_dir.mkdir()

    # Sibling process set a shared lock on Claude pool; provider must NOT be queried
    lock_until = datetime.now(UTC) + timedelta(seconds=90)
    set_shared_model_lock("claude-opus", lock_until, reason="5H_ROLLING_EXHAUSTED")

    class FailingProvider(MockProvider):
        def get_model_quota(self, *args: object, **kwargs: object):  # type: ignore[override]
            raise AssertionError("Provider should not be called when shared lock intercepts")

    no_sleep_decision = evaluate_and_gate(
        model="claude-3-7-sonnet",
        provider=FailingProvider(),
        auto_sleep=False,
        project_dir=proj_dir,
    )
    assert no_sleep_decision.exit_code == 1
    assert no_sleep_decision.action == GateAction.SLEEP_ROLLING
    assert no_sleep_decision.sleep_seconds > 0

    # Auto-sleep sleeps cleanly and clears the shared lock upon wake
    sleeps: list[float] = []
    stream = io.StringIO()
    woke_decision = evaluate_and_gate(
        model="claude-3-7-sonnet",
        provider=FailingProvider(),
        safety_buffer_seconds=5,
        auto_sleep=True,
        project_dir=proj_dir,
        sleep_fn=sleeps.append,
        is_tty=False,
        out_stream=stream,
    )
    assert woke_decision.exit_code == 0
    assert woke_decision.action == GateAction.PROCEED_FREE
    assert len(sleeps) == woke_decision.sleep_seconds
    assert check_shared_model_lock("claude-3-7-sonnet") is None


def test_evaluate_and_gate_max_sleep_and_ledger_budget_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj_dir = tmp_path / "repo-cap"
    proj_dir.mkdir()

    reset_5h = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    reset_wk = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    low_5h_provider = MockProvider(
        groups=[
            {
                "name": "Claude and GPT models",
                "buckets": [
                    {
                        "name": "5-Hour Limit",
                        "window": "5h",
                        "remaining_fraction": 0.05,
                        "reset_time": reset_5h,
                    },
                    {
                        "name": "Weekly Limit",
                        "window": "weekly",
                        "remaining_fraction": 0.80,
                        "reset_time": reset_wk,
                    },
                ],
            }
        ]
    )

    decision_capped_sleep = evaluate_and_gate(
        model="claude-3-7-sonnet",
        provider=low_5h_provider,
        rolling_threshold=15.0,
        auto_sleep=True,
        max_sleep_seconds=60,
        project_dir=proj_dir,
    )
    assert decision_capped_sleep.exit_code == 1
    assert "exceeds max_sleep_seconds=60" in decision_capped_sleep.reason
    assert check_shared_model_lock("claude-3-7-sonnet") is not None

    # Weekly depleted checks real credit spend recorded in project ledger
    record_token_transaction(
        proj_dir,
        task_id="T1",
        model="gemini-2.5-pro",
        usage=UsageTelemetry(
            work_tokens=10_000,
            estimated_cost_usd=10.0,
            is_credit_funded=True,
            credit_burn_usd=10.0,
        ),
    )
    weekly_depleted_provider = MockProvider(
        groups=[
            {
                "name": "Gemini Models",
                "buckets": [
                    {
                        "name": "5-Hour Limit",
                        "window": "5h",
                        "remaining_fraction": 0.50,
                        "reset_time": reset_5h,
                    },
                    {
                        "name": "Weekly Limit",
                        "window": "weekly",
                        "remaining_fraction": 0.04,
                        "reset_time": reset_wk,
                    },
                ],
            }
        ]
    )
    lockout_decision = evaluate_and_gate(
        model="gemini-2.5-pro",
        provider=weekly_depleted_provider,
        credit_cfg=CreditConfig(policy=CreditPolicy.BUDGET_CAPPED, max_burn_usd=10.0),
        weekly_threshold=10.0,
        project_dir=proj_dir,
    )
    assert lockout_decision.exit_code == 2
    assert lockout_decision.action == GateAction.LOCKOUT_CREDIT_BUDGET
