"""Rich status table renderer and JSON telemetry formatter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from rich.table import Table

from agymissioncontrol.config import AppConfig, CreditConfig, CreditPolicy, CreditTrigger
from agymissioncontrol.quota.models import ModelQuota, QuotaSnapshot, UsageTelemetry

_STATUS_STYLES: dict[str, str] = {
    "HEALTHY": "[green]HEALTHY[/green]",
    "LOW_ROLLING": "[yellow]LOW_ROLLING[/yellow]",
    "CREDIT_ACTIVE": "[cyan]CREDIT_ACTIVE[/cyan]",
    "WEEKLY_DEPLETED": "[red]WEEKLY_DEPLETED[/red]",
    "LOCKED_OUT": "[bold red]LOCKED_OUT[/bold red]",
}


def compute_pool_status(
    mq: ModelQuota,
    credit_cfg: CreditConfig,
    credit_spend_usd: float,
) -> str:
    """Compute display status badge for a model pool."""
    if mq.is_locked_out:
        return "LOCKED_OUT"

    credits_available = (credit_cfg.policy == CreditPolicy.ALWAYS) or (
        credit_cfg.policy == CreditPolicy.BUDGET_CAPPED
        and credit_spend_usd < credit_cfg.max_burn_usd
    )
    if mq.is_weekly_depleted:
        return "CREDIT_ACTIVE" if credits_available else "WEEKLY_DEPLETED"
    if mq.is_rolling_depleted:
        if credit_cfg.trigger == CreditTrigger.ANY and credits_available:
            return "CREDIT_ACTIVE"
        return "LOW_ROLLING"
    return "HEALTHY"


def _format_reset_epoch(reset_at: datetime | None, now: datetime) -> str:
    if reset_at is None:
        return "-"
    target = reset_at.replace(tzinfo=UTC) if reset_at.tzinfo is None else reset_at.astimezone(UTC)
    delta = target - now
    if delta <= timedelta(0):
        return "now"
    if delta < timedelta(hours=12):
        total_minutes = int(delta.total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
    return target.strftime("%a %H:%M UTC")


def _format_credits_cell(credit_cfg: CreditConfig, credit_spend_usd: float) -> str:
    if credit_cfg.policy == CreditPolicy.ALWAYS:
        return f"${credit_spend_usd:.2f} / unlimited"
    if credit_cfg.policy == CreditPolicy.NEVER:
        return f"${credit_spend_usd:.2f} / $0.00 (never)"
    return f"${credit_spend_usd:.2f} / ${credit_cfg.max_burn_usd:.2f}"


def render_status_table(
    snapshot: QuotaSnapshot,
    config: AppConfig,
    credit_spend_usd: float = 0.0,
    session_usage: UsageTelemetry | None = None,
) -> Table:
    """Render dual-tier quota, credit policy, shared lock, and status table."""
    credit_cfg = config.governor.credits
    now = datetime.now(UTC)

    caption: str | None = None
    if session_usage is not None and session_usage.total_tokens > 0:
        caption = (
            f"Session tokens: {session_usage.total_tokens:,} "
            f"(overhead {session_usage.overhead_pct:.1f}%, "
            f"cache hit {session_usage.cache_hit_rate:.1f}%, "
            f"est. ${session_usage.estimated_cost_usd:.4f})"
        )

    table = Table(
        title="agyMissionControl — Dual-Tier Quota & Credit Status",
        caption=caption,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Model Pool", style="bold", no_wrap=True)
    table.add_column("Rolling 5h (% Left)", justify="right")
    table.add_column("Rolling Reset", justify="right")
    table.add_column("Weekly Cap (% Left)", justify="right")
    table.add_column("Weekly Reset", justify="right")
    table.add_column("Credits ($ / Cap)", justify="right")
    table.add_column("Trigger Mode", no_wrap=True)
    table.add_column("Shared Lock")
    table.add_column("Status", no_wrap=True)

    credits_cell = _format_credits_cell(credit_cfg, credit_spend_usd)
    for pool_name, mq in snapshot.models.items():
        rolling_cell = f"{mq.rolling_window_remaining_pct:.0f}%"
        rolling_reset = _format_reset_epoch(mq.rolling_reset_epoch, now)
        if mq.weekly_remaining_pct is not None:
            weekly_used = max(0.0, 100.0 - mq.weekly_remaining_pct)
            weekly_cell = f"{mq.weekly_remaining_pct:.0f}% ({weekly_used:.0f}% used)"
        else:
            weekly_cell = "-"
        weekly_reset = _format_reset_epoch(mq.weekly_reset_epoch, now)
        lock_cell = mq.locked_by_reason or "Locked" if mq.is_locked_out else "-"
        status_code = compute_pool_status(mq, credit_cfg, credit_spend_usd)
        status_cell = _STATUS_STYLES.get(status_code, status_code)

        table.add_row(
            pool_name,
            rolling_cell,
            rolling_reset,
            weekly_cell,
            weekly_reset,
            credits_cell,
            credit_cfg.trigger.value,
            lock_cell,
            status_cell,
        )

    return table


def build_status_payload(
    snapshot: QuotaSnapshot,
    config: AppConfig,
    credit_spend_usd: float = 0.0,
    session_usage: UsageTelemetry | None = None,
) -> dict[str, Any]:
    """Build structured JSON payload for `agymissioncontrol status --json`."""
    credit_cfg = config.governor.credits
    models_out: dict[str, Any] = {}
    for pool_name, mq in snapshot.models.items():
        data = mq.model_dump(mode="json")
        data["status"] = compute_pool_status(mq, credit_cfg, credit_spend_usd)
        models_out[pool_name] = data

    return {
        "recorded_at": snapshot.recorded_at.isoformat(),
        "project_root": str(config.paths.project_root) if config.paths is not None else None,
        "thresholds": {
            "rolling_percent": config.governor.threshold_percent,
            "weekly_percent": config.governor.weekly_threshold_percent,
        },
        "credits": {
            "policy": credit_cfg.policy.value,
            "max_burn_usd": credit_cfg.max_burn_usd,
            "trigger": credit_cfg.trigger.value,
            "on_cap_reached": credit_cfg.on_cap_reached,
            "spend_usd": credit_spend_usd,
        },
        "session_usage": session_usage.model_dump(mode="json") if session_usage is not None else {},
        "models": models_out,
    }
