"""Quota governor decision matrix, zero-token sleep engine, and signal handlers."""

from __future__ import annotations

import math
import signal
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, TextIO

from pydantic import BaseModel
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from agymissioncontrol.config import CreditConfig, CreditPolicy, CreditTrigger
from agymissioncontrol.quota.ledger import get_credit_spend, get_session_token_totals
from agymissioncontrol.quota.locks import (
    clear_shared_model_lock,
    get_shared_model_lock_details,
    set_shared_model_lock,
)
from agymissioncontrol.quota.models import ModelQuota
from agymissioncontrol.quota.provider import QuotaProvider
from agymissioncontrol.quota.sliding_window import calculate_sleep_duration


class GateAction(StrEnum):
    """Governor decision actions for pre-flight quota evaluation."""

    PROCEED_FREE = "proceed_free"
    PROCEED_CREDIT = "proceed_credit"
    SLEEP_ROLLING = "sleep_rolling"
    LOCKOUT_WEEKLY = "lockout_weekly"
    LOCKOUT_CREDIT_BUDGET = "lockout_credit_budget"


class GateDecision(BaseModel):
    """Structured result returned by `evaluate_and_gate`."""

    action: GateAction
    reason: str
    exit_code: int
    is_credit_funded: bool = False
    sleep_seconds: int = 0
    reset_at: datetime | None = None
    model_quota: ModelQuota | None = None
    woken_early: bool = False


def evaluate_gate_decision(
    rolling_quota_pct: float,
    rolling_threshold: float,
    weekly_quota_pct: float | None,
    weekly_threshold: float,
    credit_cfg: CreditConfig,
    current_credit_burn_usd: float,
    caller_bypass_5h: bool = False,
) -> tuple[GateAction, str]:
    """Evaluate dual-tier quota thresholds against active AI credit policy."""
    if weekly_quota_pct is not None and weekly_quota_pct < weekly_threshold:
        if credit_cfg.policy == CreditPolicy.NEVER:
            return GateAction.LOCKOUT_WEEKLY, "Weekly quota depleted; AI credits disabled"
        if (
            credit_cfg.policy == CreditPolicy.BUDGET_CAPPED
            and current_credit_burn_usd >= credit_cfg.max_burn_usd
        ):
            return (
                GateAction.LOCKOUT_CREDIT_BUDGET,
                f"Weekly quota depleted; credit budget (${credit_cfg.max_burn_usd:.2f}) exhausted",
            )
        return GateAction.PROCEED_CREDIT, "Weekly quota depleted; continuing on AI credits"

    if rolling_quota_pct < rolling_threshold:
        allow_5h_credit = caller_bypass_5h or (credit_cfg.trigger == CreditTrigger.ANY)
        if allow_5h_credit and credit_cfg.policy != CreditPolicy.NEVER:
            if (
                credit_cfg.policy == CreditPolicy.BUDGET_CAPPED
                and current_credit_burn_usd >= credit_cfg.max_burn_usd
            ):
                if credit_cfg.on_cap_reached == "halt":
                    return (
                        GateAction.LOCKOUT_CREDIT_BUDGET,
                        f"5h rolling limit depleted; credit budget (${credit_cfg.max_burn_usd:.2f})"
                        " exhausted (on_cap_reached=halt)",
                    )
                return (
                    GateAction.SLEEP_ROLLING,
                    "5h rolling limit depleted; credit budget cap reached, falling back to sleep",
                )
            return (
                GateAction.PROCEED_CREDIT,
                "5h rolling limit depleted; bypassing via AI credits as requested",
            )
        return (
            GateAction.SLEEP_ROLLING,
            "5h rolling limit depleted; sleeping until free recovery (default zero-token policy)",
        )

    return GateAction.PROCEED_FREE, "Quota healthy"


def _ensure_utc_dt(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _format_utc_iso(dt: datetime) -> str:
    utc_dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit_event(
    on_event: Callable[[str, dict[str, Any]], None] | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Invoke optional governor event callback cleanly when configured."""
    if on_event is not None:
        on_event(event_type, payload)


def execute_sleep(
    seconds: int,
    model_name: str,
    wakes_at: datetime | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    is_tty: bool | None = None,
    out_stream: TextIO | None = None,
) -> bool:
    """Sleep with zero token burn, TTY/headless rendering, and signal handling.

    Returns ``True`` if the full sleep duration completed, or ``False`` if woken
    early by ``SIGUSR1``. Raises ``SystemExit(130)`` on ``SIGINT`` or ``SIGTERM``.
    """
    if seconds <= 0:
        return True

    stream = out_stream or sys.stdout
    tty = stream.isatty() if is_tty is None else is_tty
    target_wake = wakes_at or (datetime.now(UTC) + timedelta(seconds=seconds))
    wake_iso = _format_utc_iso(target_wake)

    wake_requested = False
    interrupted = False
    is_main_thread = threading.current_thread() is threading.main_thread()
    previous_handlers: dict[int, Any] = {}

    # Signal handlers must stay async-signal/reentrancy-safe (flags only; no Rich/stream I/O).
    def _interrupt_handler(_signum: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True

    def _wake_handler(_signum: int, _frame: Any) -> None:
        nonlocal wake_requested
        wake_requested = True

    if is_main_thread:
        for sig, handler in (
            (signal.SIGINT, _interrupt_handler),
            (signal.SIGTERM, _interrupt_handler),
        ):
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, handler)
        if hasattr(signal, "SIGUSR1"):
            previous_handlers[signal.SIGUSR1] = signal.getsignal(signal.SIGUSR1)
            signal.signal(signal.SIGUSR1, _wake_handler)

    start_mono = time.monotonic()
    deadline_mono = start_mono + seconds

    try:
        console: Console | None = None
        if tty:
            console = Console(file=stream, force_terminal=True, highlight=False)
            with Progress(
                TextColumn("[cyan]Zero-token sleep[/cyan]"),
                TextColumn("[bold]{task.description}[/bold]"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}s"),
                TimeRemainingColumn(),
                TextColumn(f"until {wake_iso}"),
                console=console,
                transient=True,
            ) as progress:
                task_id = progress.add_task(model_name, total=seconds)
                elapsed = 0
                while elapsed < seconds and not wake_requested and not interrupted:
                    if sleep_fn is time.sleep and time.monotonic() >= deadline_mono:
                        break
                    sleep_fn(1.0)
                    if wake_requested or interrupted:
                        break
                    if sleep_fn is time.sleep:
                        new_elapsed = min(seconds, math.floor(time.monotonic() - start_mono))
                        delta = max(1, new_elapsed - elapsed)
                    else:
                        delta = 1
                    elapsed = min(seconds, elapsed + delta)
                    progress.update(task_id, completed=elapsed)
            if not interrupted:
                if wake_requested:
                    console.print(f"[cyan]Early wake signal received for {model_name}.[/cyan]")
                else:
                    console.print(
                        f"[green]Quota window reset for {model_name} ({wake_iso}).[/green]"
                    )
        else:
            stream.write(
                f"[SLEEP] Model {model_name} quota exhausted. "
                f"Sleeping {seconds}s until {wake_iso}...\n"
            )
            stream.flush()
            elapsed = 0
            last_heartbeat_bucket = 0
            while elapsed < seconds and not wake_requested and not interrupted:
                if sleep_fn is time.sleep and time.monotonic() >= deadline_mono:
                    break
                sleep_fn(1.0)
                if wake_requested or interrupted:
                    break
                if sleep_fn is time.sleep:
                    elapsed = min(
                        seconds, max(elapsed + 1, math.floor(time.monotonic() - start_mono))
                    )
                else:
                    elapsed += 1
                remaining = max(0, seconds - elapsed)
                bucket = elapsed // 60
                if remaining > 0 and bucket > last_heartbeat_bucket:
                    last_heartbeat_bucket = bucket
                    mins_left = remaining // 60
                    secs_left = remaining % 60
                    stream.write(
                        f"[SLEEP HEARTBEAT] {model_name}: {mins_left}m {secs_left}s remaining "
                        f"(until {wake_iso}).\n"
                    )
                    stream.flush()

            if not interrupted:
                if wake_requested:
                    stream.write(f"[SLEEP WAKE] Early wake signal received for {model_name}.\n")
                else:
                    stream.write(
                        f"[SLEEP WAKE] Quota window reset for {model_name} ({wake_iso}).\n"
                    )
                stream.flush()

        if interrupted:
            if console is not None:
                console.print("\n[yellow]Sleep cancelled by user. Exiting safely...[/yellow]")
            else:
                stream.write("\n[SLEEP CANCELLED] Interrupted by signal. Exiting safely.\n")
                stream.flush()
            sys.exit(130)
    finally:
        for sig, old_handler in previous_handlers.items():
            signal.signal(sig, old_handler)

    return not wake_requested


def evaluate_and_gate(
    model: str,
    provider: QuotaProvider,
    credit_cfg: CreditConfig | None = None,
    rolling_threshold: float = 15.0,
    weekly_threshold: float = 10.0,
    safety_buffer_seconds: int = 60,
    auto_sleep: bool = True,
    max_sleep_seconds: int | None = None,
    caller_bypass_5h: bool = False,
    project_dir: Path | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    is_tty: bool | None = None,
    out_stream: TextIO | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    max_session_tokens: int | None = None,
) -> GateDecision:
    """Evaluate shared locks and live quota, then gate, sleep, or fail closed."""
    active_credit_cfg = credit_cfg or CreditConfig()
    current_burn = get_credit_spend(project_dir) if project_dir is not None else 0.0
    if max_session_tokens is not None and project_dir is not None:
        session_totals = get_session_token_totals(project_dir)
        if session_totals.total_tokens >= max_session_tokens:
            cap_reason = (
                f"Session token ceiling reached ({session_totals.total_tokens:,} >= "
                f"{max_session_tokens:,} tokens)"
            )
            _emit_event(
                on_event,
                "HARD_LOCKOUT",
                {
                    "model": model,
                    "action": GateAction.LOCKOUT_CREDIT_BUDGET.value,
                    "reason": cap_reason,
                    "session_tokens": session_totals.total_tokens,
                    "max_session_tokens": max_session_tokens,
                    "source": "session_token_cap",
                },
            )
            return GateDecision(
                action=GateAction.LOCKOUT_CREDIT_BUDGET,
                reason=cap_reason,
                exit_code=2,
                is_credit_funded=False,
            )

    # Pre-flight shared model lock check before invoking external telemetry CLI
    lock_details = get_shared_model_lock_details(model)
    if lock_details is not None:
        lock_until: datetime = lock_details["locked_until"]
        lock_reason = str(lock_details["reason"])
        allow_5h_credit = caller_bypass_5h or (active_credit_cfg.trigger == CreditTrigger.ANY)
        credits_available = active_credit_cfg.policy == CreditPolicy.ALWAYS or (
            active_credit_cfg.policy == CreditPolicy.BUDGET_CAPPED
            and current_burn < active_credit_cfg.max_burn_usd
        )
        is_weekly_lock = "WEEKLY" in lock_reason.upper()

        if is_weekly_lock:
            if credits_available:
                return GateDecision(
                    action=GateAction.PROCEED_CREDIT,
                    reason=f"Shared lock ({lock_reason}) bypassed via AI credits",
                    exit_code=0,
                    is_credit_funded=True,
                    reset_at=lock_until,
                )
            lockout_reason = (
                f"Shared weekly pool lockout active until {_format_utc_iso(lock_until)}"
            )
            _emit_event(
                on_event,
                "HARD_LOCKOUT",
                {
                    "model": model,
                    "reason": lockout_reason,
                    "reset_at": _format_utc_iso(lock_until),
                    "source": "shared_lock",
                },
            )
            return GateDecision(
                action=GateAction.LOCKOUT_WEEKLY,
                reason=lockout_reason,
                exit_code=2,
                is_credit_funded=False,
                reset_at=lock_until,
            )

        if allow_5h_credit and credits_available:
            return GateDecision(
                action=GateAction.PROCEED_CREDIT,
                reason="Shared 5h rolling lock bypassed via AI credits",
                exit_code=0,
                is_credit_funded=True,
                reset_at=lock_until,
            )
        if (
            allow_5h_credit
            and active_credit_cfg.policy == CreditPolicy.BUDGET_CAPPED
            and current_burn >= active_credit_cfg.max_burn_usd
            and active_credit_cfg.on_cap_reached == "halt"
        ):
            cap_reason = (
                f"Shared 5h rolling lock active; credit budget "
                f"(${active_credit_cfg.max_burn_usd:.2f}) exhausted (on_cap_reached=halt)"
            )
            _emit_event(
                on_event,
                "CREDIT_BUDGET_EXCEEDED",
                {
                    "model": model,
                    "reason": cap_reason,
                    "current_burn_usd": current_burn,
                    "max_burn_usd": active_credit_cfg.max_burn_usd,
                    "reset_at": _format_utc_iso(lock_until),
                },
            )
            _emit_event(
                on_event,
                "HARD_LOCKOUT",
                {
                    "model": model,
                    "action": GateAction.LOCKOUT_CREDIT_BUDGET.value,
                    "reason": cap_reason,
                    "reset_at": _format_utc_iso(lock_until),
                    "source": "shared_lock",
                },
            )
            return GateDecision(
                action=GateAction.LOCKOUT_CREDIT_BUDGET,
                reason=cap_reason,
                exit_code=2,
                is_credit_funded=False,
                reset_at=lock_until,
            )

        sleep_seconds = calculate_sleep_duration(
            lock_until, safety_buffer_seconds=safety_buffer_seconds
        )
        if max_sleep_seconds is not None and sleep_seconds > max_sleep_seconds:
            return GateDecision(
                action=GateAction.SLEEP_ROLLING,
                reason=(
                    f"Shared pool lock active; sleep {sleep_seconds}s exceeds "
                    f"max_sleep_seconds={max_sleep_seconds}"
                ),
                exit_code=1,
                sleep_seconds=sleep_seconds,
                reset_at=lock_until,
            )
        if not auto_sleep:
            return GateDecision(
                action=GateAction.SLEEP_ROLLING,
                reason=f"Shared pool lock active until {_format_utc_iso(lock_until)}",
                exit_code=1,
                sleep_seconds=sleep_seconds,
                reset_at=lock_until,
            )

        _emit_event(
            on_event,
            "SLEEP_ENTERED",
            {
                "model": model,
                "sleep_seconds": sleep_seconds,
                "wakes_at": _format_utc_iso(lock_until),
                "source": "shared_lock",
            },
        )
        completed = execute_sleep(
            sleep_seconds,
            model,
            wakes_at=lock_until,
            sleep_fn=sleep_fn,
            is_tty=is_tty,
            out_stream=out_stream,
        )
        if completed is False:
            clear_shared_model_lock(model)
            _emit_event(
                on_event,
                "SLEEP_WOKEN",
                {"model": model, "source": "shared_lock", "wakes_at": _format_utc_iso(lock_until)},
            )
            return GateDecision(
                action=GateAction.SLEEP_ROLLING,
                reason="Woken early by SIGUSR1; re-evaluating quota",
                exit_code=1,
                sleep_seconds=sleep_seconds,
                reset_at=lock_until,
                woken_early=True,
            )
        clear_shared_model_lock(model, max_locked_until=lock_until)
        return GateDecision(
            action=GateAction.PROCEED_FREE,
            reason="Recovered from shared pool lock wait",
            exit_code=0,
            sleep_seconds=sleep_seconds,
            reset_at=lock_until,
        )

    # No active shared lock; retrieve authoritative model quota from provider
    quota = provider.get_model_quota(
        model,
        rolling_threshold=rolling_threshold,
        weekly_threshold=weekly_threshold,
        respect_shared_lock=False,
    )
    action, reason = evaluate_gate_decision(
        rolling_quota_pct=quota.rolling_window_remaining_pct,
        rolling_threshold=rolling_threshold,
        weekly_quota_pct=quota.weekly_remaining_pct,
        weekly_threshold=weekly_threshold,
        credit_cfg=active_credit_cfg,
        current_credit_burn_usd=current_burn,
        caller_bypass_5h=caller_bypass_5h,
    )

    if action == GateAction.PROCEED_FREE:
        return GateDecision(
            action=action,
            reason=reason,
            exit_code=0,
            is_credit_funded=False,
            model_quota=quota,
        )

    if action == GateAction.PROCEED_CREDIT:
        return GateDecision(
            action=action,
            reason=reason,
            exit_code=0,
            is_credit_funded=True,
            model_quota=quota,
        )

    if action in (GateAction.LOCKOUT_WEEKLY, GateAction.LOCKOUT_CREDIT_BUDGET):
        reset_at = _ensure_utc_dt(quota.weekly_reset_epoch or quota.rolling_reset_epoch)
        if reset_at is not None and reset_at > datetime.now(UTC):
            set_shared_model_lock(model, reset_at, reason="WEEKLY_LOCKOUT")
        reset_iso = _format_utc_iso(reset_at) if reset_at else None
        if action == GateAction.LOCKOUT_CREDIT_BUDGET:
            _emit_event(
                on_event,
                "CREDIT_BUDGET_EXCEEDED",
                {
                    "model": model,
                    "reason": reason,
                    "current_burn_usd": current_burn,
                    "max_burn_usd": active_credit_cfg.max_burn_usd,
                    "reset_at": reset_iso,
                },
            )
        _emit_event(
            on_event,
            "HARD_LOCKOUT",
            {
                "model": model,
                "action": action.value,
                "reason": reason,
                "reset_at": reset_iso,
                "source": "provider",
            },
        )
        return GateDecision(
            action=action,
            reason=reason,
            exit_code=2,
            is_credit_funded=False,
            reset_at=reset_at,
            model_quota=quota,
        )

    # SLEEP_ROLLING branch
    reset_at = _ensure_utc_dt(quota.rolling_reset_epoch) or (
        datetime.now(UTC) + timedelta(minutes=5)
    )
    sleep_seconds = calculate_sleep_duration(reset_at, safety_buffer_seconds=safety_buffer_seconds)
    if sleep_seconds > 0:
        set_shared_model_lock(model, reset_at, reason="5H_ROLLING_EXHAUSTED")

    if max_sleep_seconds is not None and sleep_seconds > max_sleep_seconds:
        return GateDecision(
            action=GateAction.SLEEP_ROLLING,
            reason=(
                f"{reason} (sleep {sleep_seconds}s exceeds max_sleep_seconds={max_sleep_seconds})"
            ),
            exit_code=1,
            sleep_seconds=sleep_seconds,
            reset_at=reset_at,
            model_quota=quota,
        )

    if not auto_sleep:
        return GateDecision(
            action=GateAction.SLEEP_ROLLING,
            reason=reason,
            exit_code=1,
            sleep_seconds=sleep_seconds,
            reset_at=reset_at,
            model_quota=quota,
        )

    _emit_event(
        on_event,
        "SLEEP_ENTERED",
        {
            "model": model,
            "sleep_seconds": sleep_seconds,
            "wakes_at": _format_utc_iso(reset_at),
            "source": "provider",
        },
    )
    completed = execute_sleep(
        sleep_seconds,
        model,
        wakes_at=reset_at,
        sleep_fn=sleep_fn,
        is_tty=is_tty,
        out_stream=out_stream,
    )
    if completed is False:
        clear_shared_model_lock(model)
        _emit_event(
            on_event,
            "SLEEP_WOKEN",
            {"model": model, "source": "provider", "wakes_at": _format_utc_iso(reset_at)},
        )
        return GateDecision(
            action=GateAction.SLEEP_ROLLING,
            reason="Woken early by SIGUSR1; re-evaluating quota",
            exit_code=1,
            sleep_seconds=sleep_seconds,
            reset_at=reset_at,
            model_quota=quota,
            woken_early=True,
        )
    clear_shared_model_lock(model, max_locked_until=reset_at)
    return GateDecision(
        action=GateAction.PROCEED_FREE,
        reason="Recovered from 5h rolling quota sleep",
        exit_code=0,
        is_credit_funded=False,
        sleep_seconds=sleep_seconds,
        reset_at=reset_at,
        model_quota=quota,
    )
