"""Authoritative sleep duration math and directional quota impact helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from agymissioncontrol.quota.locks import resolve_quota_group
from agymissioncontrol.quota.provider import DEFAULT_POOL_CAPACITIES


def calculate_sleep_duration(
    reset_at: datetime,
    safety_buffer_seconds: int = 60,
    now_utc: datetime | None = None,
) -> int:
    """Derive exact sleep seconds directly from the Google-reported reset timestamp.

    Returns ``0`` when ``now >= reset_at``; otherwise returns
    ``int((reset_at - now).total_seconds()) + max(0, safety_buffer_seconds)``.
    """
    current = now_utc or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    else:
        current = current.astimezone(UTC)

    target = reset_at
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    else:
        target = target.astimezone(UTC)

    delta_seconds = (target - current).total_seconds()
    if delta_seconds <= 0:
        return 0
    return int(delta_seconds) + max(0, safety_buffer_seconds)


def estimate_directional_allowance_pct(total_tokens: int, model: str) -> float:
    """Compute directional pool allowance percentage (informational telemetry only).

    Never used for go/no-go gating decisions, which strictly rely on authoritative
    values from `agy -p '/usage' --output-format json`.
    """
    group = resolve_quota_group(model)
    capacity = DEFAULT_POOL_CAPACITIES.get(group, 1_000_000)
    if capacity <= 0 or total_tokens <= 0:
        return 0.0
    return round((total_tokens / capacity) * 100.0, 4)
