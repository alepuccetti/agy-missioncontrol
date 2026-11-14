"""Pydantic data models for quota snapshots, lockouts, and token/overhead telemetry."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class ModelQuota(BaseModel):
    """Normalized dual-tier quota availability for a single model or shared quota pool."""

    model_name: str
    quota_group: str
    rolling_window_remaining_pct: float = Field(ge=0.0, le=100.0)
    rolling_reset_epoch: datetime | None = None
    weekly_remaining_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    weekly_reset_epoch: datetime | None = None
    is_locked_out: bool = False
    locked_by_reason: str | None = None
    is_weekly_depleted: bool = False
    is_rolling_depleted: bool = False


class QuotaSnapshotRecord(BaseModel):
    """Single time-series quota observation parsed from `agy -p '/usage' --output-format json`."""

    model_family: str
    limit_type: Literal["five_hour", "weekly"]
    remaining_pct: float = Field(ge=0.0, le=100.0)
    reset_at: datetime
    recorded_at: datetime
    raw_line: str = ""


class QuotaSnapshot(BaseModel):
    """Full quota telemetry snapshot across all reported model pools."""

    records: list[QuotaSnapshotRecord] = Field(default_factory=list)
    models: dict[str, ModelQuota] = Field(default_factory=dict)
    recorded_at: datetime


class PricingModel(BaseModel):
    """Per-1M token pricing rates in USD."""

    input_per_1m: float = Field(ge=0.0)
    output_per_1m: float = Field(ge=0.0)
    cached_input_per_1m: float = Field(default=0.0, ge=0.0)


class QuotaImpact(BaseModel):
    """Informational directional token cost and pool allowance estimate."""

    model: str
    estimated_tokens: int = Field(ge=0)
    directional_allowance_pct: float = Field(ge=0.0)
    estimated_cost_usd: float = Field(ge=0.0)


class UsageTelemetry(BaseModel):
    """Granular token, overhead, cache efficiency, and credit spend accounting."""

    prompt_tokens: int = Field(default=0, ge=0)
    candidate_tokens: int = Field(default=0, ge=0)
    thinking_tokens: int = Field(default=0, ge=0)
    work_tokens: int = Field(default=0, ge=0)
    overhead_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    retry_tax_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)
    is_credit_funded: bool = False
    credit_burn_usd: float = Field(default=0.0, ge=0.0)
    credit_trigger_reason: str | None = None
    allowance_pct_spent: float = Field(default=0.0, ge=0.0)

    @computed_field
    @property
    def total_tokens(self) -> int:
        """Total billed execution tokens (`work_tokens + overhead_tokens`)."""
        return self.work_tokens + self.overhead_tokens

    @computed_field
    @property
    def overhead_pct(self) -> float:
        """Percentage of total tokens spent on harness/retry overhead."""
        total = self.total_tokens
        if total == 0:
            return 0.0
        return round((self.overhead_tokens / total) * 100.0, 2)

    @computed_field
    @property
    def cache_hit_rate(self) -> float:
        """Prompt cache hit ratio percentage."""
        denominator = self.prompt_tokens + self.cache_read_tokens
        if denominator == 0:
            return 0.0
        return round((self.cache_read_tokens / denominator) * 100.0, 2)
