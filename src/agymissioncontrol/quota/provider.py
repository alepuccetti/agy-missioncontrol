"""Authoritative `agy` CLI quota provider, mock provider, and cost accounting."""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agymissioncontrol.quota.locks import (
    get_shared_model_lock_details,
    resolve_quota_group,
)
from agymissioncontrol.quota.models import (
    ModelQuota,
    PricingModel,
    QuotaImpact,
    QuotaSnapshot,
    QuotaSnapshotRecord,
    UsageTelemetry,
)

DEFAULT_PRICING: dict[str, PricingModel] = {
    "claude-3-7-sonnet": PricingModel(
        input_per_1m=3.00,
        output_per_1m=15.00,
        cached_input_per_1m=0.30,
    ),
    "gemini-2.5-pro": PricingModel(
        input_per_1m=1.25,
        output_per_1m=10.00,
        cached_input_per_1m=0.31,
    ),
    "gemini-2.5-flash": PricingModel(
        input_per_1m=0.15,
        output_per_1m=0.60,
        cached_input_per_1m=0.04,
    ),
}

DEFAULT_POOL_CAPACITIES: dict[str, int] = {
    "Claude and GPT models": 1_000_000,
    "Gemini 4 Argon": 1_500_000,
    "Gemini Models": 2_000_000,
}


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _resolve_pricing(
    model: str,
    pricing_overrides: dict[str, PricingModel] | None = None,
) -> PricingModel:
    table = pricing_overrides or DEFAULT_PRICING
    normalized = model.lower().strip()
    if model in table:
        return table[model]
    if normalized in table:
        return table[normalized]
    if "flash" in normalized:
        return table.get("gemini-2.5-flash", DEFAULT_PRICING["gemini-2.5-flash"])
    group = resolve_quota_group(model)
    if group == "Claude and GPT models":
        return table.get("claude-3-7-sonnet", DEFAULT_PRICING["claude-3-7-sonnet"])
    return table.get("gemini-2.5-pro", DEFAULT_PRICING["gemini-2.5-pro"])


def calculate_cost_usd(
    usage: UsageTelemetry,
    model: str,
    pricing_overrides: dict[str, PricingModel] | None = None,
) -> float:
    """Compute estimated USD cost from token breakdowns and model pricing rates."""
    pricing = _resolve_pricing(model, pricing_overrides=pricing_overrides)
    if usage.prompt_tokens > 0 or usage.candidate_tokens > 0 or usage.thinking_tokens > 0:
        input_cost = (usage.prompt_tokens / 1_000_000) * pricing.input_per_1m
        output_tokens = usage.candidate_tokens + usage.thinking_tokens
        output_cost = (output_tokens / 1_000_000) * pricing.output_per_1m
        cached_cost = (usage.cache_read_tokens / 1_000_000) * pricing.cached_input_per_1m
        return round(input_cost + output_cost + cached_cost, 6)

    if usage.total_tokens > 0:
        return round((usage.total_tokens / 1_000_000) * pricing.input_per_1m, 6)

    return 0.0


def estimate_quota_impact(
    work_tokens: int,
    overhead_tokens: int,
    model: str,
    pricing_overrides: dict[str, PricingModel] | None = None,
) -> QuotaImpact:
    """Compute directional pool allowance % and dollar cost (informational only)."""
    total = max(0, work_tokens) + max(0, overhead_tokens)
    group = resolve_quota_group(model)
    capacity = DEFAULT_POOL_CAPACITIES.get(group, 1_000_000)
    allowance_pct = round((total / capacity) * 100.0, 4) if capacity > 0 else 0.0
    pricing = _resolve_pricing(model, pricing_overrides=pricing_overrides)
    cost = round((total / 1_000_000) * pricing.input_per_1m, 6)
    return QuotaImpact(
        model=model,
        estimated_tokens=total,
        directional_allowance_pct=allowance_pct,
        estimated_cost_usd=cost,
    )


def fetch_usage_json() -> dict[str, Any]:
    """Query `agy -p '/usage' --output-format json` in a headless subprocess."""
    try:
        completed = subprocess.run(
            ["agy", "-p", "/usage", "--output-format", "json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out querying `agy -p '/usage' --output-format json`") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(
            f"`agy -p '/usage' --output-format json` exited with {exc.returncode}: {stderr}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Failed executing `agy` binary: {exc}") from exc

    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Malformed JSON from `agy -p '/usage' --output-format json`: {exc}"
        ) from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            "Expected top-level JSON object from `agy -p '/usage' --output-format json`"
        )
    return parsed


def _parse_iso_utc(raw: Any, field_name: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"Missing or invalid ISO timestamp for `{field_name}`: {raw!r}")
    normalized = raw.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO timestamp for `{field_name}`: {raw!r}") from exc
    return _ensure_utc(dt)


def _parse_groups_payload(
    groups: Any,
    now_utc: datetime,
) -> list[QuotaSnapshotRecord]:
    if not isinstance(groups, list):
        raise ValueError("'command.data.groups' in usage payload must be a list")

    records: list[QuotaSnapshotRecord] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("name"), str):
            raise ValueError(f"Invalid quota group entry: {group!r}")

        canonical_family = resolve_quota_group(group["name"])
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            raise ValueError(f"'buckets' must be a list for group {group['name']!r}")

        for bucket in buckets:
            if not isinstance(bucket, dict):
                raise ValueError(f"Invalid bucket entry in group {group['name']!r}")
            window = str(bucket.get("window", "")).strip().lower()
            if window in ("5h", "five_hour"):
                limit_type = "five_hour"
            elif window == "weekly":
                limit_type = "weekly"
            else:
                raise ValueError(f"Unsupported bucket window {window!r} in group {group['name']!r}")

            if "remaining_fraction" not in bucket:
                raise ValueError(f"Missing 'remaining_fraction' in {group['name']}.{window}")
            try:
                remaining_pct = round(float(bucket["remaining_fraction"]) * 100.0, 2)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid 'remaining_fraction' in {group['name']}.{window}"
                ) from exc

            if not 0.0 <= remaining_pct <= 100.0:
                raise ValueError(
                    f"Out-of-range remaining percentage {remaining_pct} in {group['name']}.{window}"
                )

            reset_raw = bucket.get("reset_time")
            reset_at = _parse_iso_utc(reset_raw, f"{group['name']}.{window}.reset_time")
            bucket_name = bucket.get("name", window)
            records.append(
                QuotaSnapshotRecord(
                    model_family=canonical_family,
                    limit_type=limit_type,
                    remaining_pct=remaining_pct,
                    reset_at=reset_at,
                    recorded_at=now_utc,
                    raw_line=f"{canonical_family} | {bucket_name} | {remaining_pct}% | {reset_raw}",
                )
            )
    return records


def _parse_models_payload(
    models_obj: Any,
    now_utc: datetime,
) -> list[QuotaSnapshotRecord]:
    if not isinstance(models_obj, dict):
        raise ValueError("'models' field in usage payload must be a JSON object")

    records: list[QuotaSnapshotRecord] = []
    for model_family, windows in models_obj.items():
        if not isinstance(model_family, str) or not isinstance(windows, dict):
            raise ValueError(f"Invalid model quota entry for {model_family!r}")

        canonical_family = resolve_quota_group(model_family)
        for limit_type in ("five_hour", "weekly"):
            window_data = windows.get(limit_type)
            if window_data is None:
                continue
            if not isinstance(window_data, dict):
                raise ValueError(f"Expected object for {model_family}.{limit_type}")
            if "remaining_pct" not in window_data:
                raise ValueError(f"Missing 'remaining_pct' in {model_family}.{limit_type}")
            try:
                remaining_pct = float(window_data["remaining_pct"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid 'remaining_pct' in {model_family}.{limit_type}") from exc
            reset_at = _parse_iso_utc(window_data.get("reset_at"), f"{model_family}.{limit_type}")
            records.append(
                QuotaSnapshotRecord(
                    model_family=canonical_family,
                    limit_type=limit_type,
                    remaining_pct=remaining_pct,
                    reset_at=reset_at,
                    recorded_at=now_utc,
                    raw_line=json.dumps(window_data, sort_keys=True),
                )
            )
    return records


def parse_usage_json(
    payload: dict[str, Any],
    recorded_at: datetime | None = None,
) -> list[QuotaSnapshotRecord]:
    """Parse `agy -p '/usage' --output-format json` into `QuotaSnapshotRecord` entries."""
    if not isinstance(payload, dict):
        raise ValueError("Usage payload must be a JSON object")

    now_utc = _ensure_utc(recorded_at or datetime.now(UTC))

    if "command" in payload:
        command = payload.get("command")
        if not isinstance(command, dict) or not isinstance(command.get("data"), dict):
            raise ValueError("Usage payload 'command' must contain a 'data' object")
        data = command["data"]
        if "groups" not in data:
            raise ValueError("Usage payload 'command.data' must contain 'groups'")
        return _parse_groups_payload(data["groups"], now_utc)

    if "models" in payload:
        return _parse_models_payload(payload["models"], now_utc)

    raise ValueError("Usage payload must contain 'command.data.groups' or 'models'")


def _persist_snapshot_records(
    records: list[QuotaSnapshotRecord],
    project_dir: Path | None = None,
) -> None:
    from agymissioncontrol.quota.ledger import record_quota_snapshots

    record_quota_snapshots(records, project_dir=project_dir)


def _build_models_from_records(
    records: list[QuotaSnapshotRecord],
    rolling_threshold: float,
    weekly_threshold: float,
) -> dict[str, ModelQuota]:
    grouped: dict[str, dict[str, QuotaSnapshotRecord]] = {}
    for rec in records:
        bucket = grouped.setdefault(rec.model_family, {})
        existing = bucket.get(rec.limit_type)
        if existing is None:
            bucket[rec.limit_type] = rec
            continue
        merged_reset = (
            max(_ensure_utc(existing.reset_at), _ensure_utc(rec.reset_at))
            if existing.reset_at is not None and rec.reset_at is not None
            else _ensure_utc(existing.reset_at)
            if existing.reset_at is not None
            else (_ensure_utc(rec.reset_at) if rec.reset_at is not None else None)
        )
        bucket[rec.limit_type] = QuotaSnapshotRecord(
            model_family=rec.model_family,
            limit_type=rec.limit_type,
            remaining_pct=min(existing.remaining_pct, rec.remaining_pct),
            reset_at=merged_reset,
            recorded_at=max(_ensure_utc(existing.recorded_at), _ensure_utc(rec.recorded_at)),
            raw_line=f"{existing.raw_line} | {rec.raw_line}",
        )

    models: dict[str, ModelQuota] = {}
    for family, windows in grouped.items():
        five_hour = windows.get("five_hour")
        weekly = windows.get("weekly")
        rolling_pct = five_hour.remaining_pct if five_hour is not None else 100.0
        rolling_reset = _ensure_utc(five_hour.reset_at) if five_hour is not None else None
        weekly_pct = weekly.remaining_pct if weekly is not None else None
        weekly_reset = (
            _ensure_utc(weekly.reset_at) if weekly is not None and weekly.reset_at else None
        )

        lock_details = get_shared_model_lock_details(family)
        is_locked = lock_details is not None
        locked_reason = lock_details["reason"] if lock_details is not None else None
        if lock_details is not None and (
            rolling_reset is None or _ensure_utc(lock_details["locked_until"]) > rolling_reset
        ):
            rolling_reset = _ensure_utc(lock_details["locked_until"])

        models[family] = ModelQuota(
            model_name=family,
            quota_group=family,
            rolling_window_remaining_pct=rolling_pct,
            rolling_reset_epoch=rolling_reset,
            weekly_remaining_pct=weekly_pct,
            weekly_reset_epoch=weekly_reset,
            is_locked_out=is_locked,
            locked_by_reason=locked_reason,
            is_weekly_depleted=(weekly_pct is not None and weekly_pct < weekly_threshold),
            is_rolling_depleted=rolling_pct < rolling_threshold,
        )

    return models


def _build_shared_lock_quota(
    model_name: str,
    group: str,
    lock_details: dict[str, Any],
) -> ModelQuota:
    reason = str(lock_details["reason"])
    is_weekly = reason in {"WEEKLY_LOCKOUT", "CREDIT_CAP_REACHED"}
    locked_until = _ensure_utc(lock_details["locked_until"])
    return ModelQuota(
        model_name=model_name,
        quota_group=group,
        rolling_window_remaining_pct=0.0,
        rolling_reset_epoch=None if is_weekly else locked_until,
        weekly_remaining_pct=0.0 if is_weekly else None,
        weekly_reset_epoch=locked_until if is_weekly else None,
        is_locked_out=True,
        locked_by_reason=reason,
        is_weekly_depleted=is_weekly,
        is_rolling_depleted=not is_weekly,
    )


class QuotaProvider(ABC):
    """Abstract interface for retrieving dual-tier quota telemetry."""

    @abstractmethod
    def get_snapshot(
        self,
        rolling_threshold: float = 15.0,
        weekly_threshold: float = 10.0,
    ) -> QuotaSnapshot:
        """Retrieve a full telemetry snapshot across model pools."""

    @abstractmethod
    def get_model_quota(
        self,
        model_name: str,
        rolling_threshold: float = 15.0,
        weekly_threshold: float = 10.0,
        respect_shared_lock: bool = True,
    ) -> ModelQuota:
        """Retrieve quota state for `model_name`, resolved to its canonical quota pool."""


class AgyCliProvider(QuotaProvider):
    """Authoritative telemetry provider backed by `agy -p '/usage' --output-format json`."""

    def __init__(
        self,
        project_dir: Path | None = None,
        fetcher: Callable[[], dict[str, Any]] = fetch_usage_json,
    ) -> None:
        self.project_dir = project_dir
        self._fetcher = fetcher

    def get_snapshot(
        self,
        rolling_threshold: float = 15.0,
        weekly_threshold: float = 10.0,
    ) -> QuotaSnapshot:
        now_utc = datetime.now(UTC)
        payload = self._fetcher()
        records = parse_usage_json(payload, recorded_at=now_utc)
        _persist_snapshot_records(records, project_dir=self.project_dir)
        models = _build_models_from_records(
            records,
            rolling_threshold=rolling_threshold,
            weekly_threshold=weekly_threshold,
        )
        return QuotaSnapshot(records=records, models=models, recorded_at=now_utc)

    def get_model_quota(
        self,
        model_name: str,
        rolling_threshold: float = 15.0,
        weekly_threshold: float = 10.0,
        respect_shared_lock: bool = True,
    ) -> ModelQuota:
        group = resolve_quota_group(model_name)
        if respect_shared_lock:
            lock_details = get_shared_model_lock_details(group)
            if lock_details is not None:
                return _build_shared_lock_quota(model_name, group, lock_details)

        snapshot = self.get_snapshot(
            rolling_threshold=rolling_threshold,
            weekly_threshold=weekly_threshold,
        )
        pool_quota = snapshot.models.get(group)
        if pool_quota is None:
            raise KeyError(
                f"Quota pool {group!r} (for model {model_name!r}) missing from `agy` usage response"
            )
        return pool_quota.model_copy(update={"model_name": model_name})


class MockProvider(QuotaProvider):
    """Deterministic in-memory provider for offline tests and CLI `--mock` previews."""

    def __init__(
        self,
        groups: list[dict[str, Any]] | None = None,
        models_payload: dict[str, dict[str, Any]] | None = None,
        project_dir: Path | None = None,
    ) -> None:
        now_utc = datetime.now(UTC)
        default_five_reset = (now_utc + timedelta(hours=3, minutes=45)).isoformat()
        default_weekly_reset = (now_utc + timedelta(days=4, hours=12)).isoformat()
        self.project_dir = project_dir

        if groups is not None:
            self._groups = groups
        elif models_payload is not None:
            converted: list[dict[str, Any]] = []
            for family, windows in models_payload.items():
                buckets: list[dict[str, Any]] = []
                if "five_hour" in windows:
                    w = windows["five_hour"]
                    buckets.append(
                        {
                            "name": "5-Hour Limit",
                            "window": "5h",
                            "remaining_fraction": float(w["remaining_pct"]) / 100.0,
                            "reset_time": w["reset_at"],
                        }
                    )
                if "weekly" in windows:
                    w = windows["weekly"]
                    buckets.append(
                        {
                            "name": "Weekly Limit",
                            "window": "weekly",
                            "remaining_fraction": float(w["remaining_pct"]) / 100.0,
                            "reset_time": w["reset_at"],
                        }
                    )
                converted.append({"name": family, "buckets": buckets})
            self._groups = converted
        else:
            self._groups = [
                {
                    "name": "Claude and GPT models",
                    "buckets": [
                        {
                            "name": "5-Hour Limit",
                            "window": "5h",
                            "remaining_fraction": 0.85,
                            "reset_time": default_five_reset,
                        },
                        {
                            "name": "Weekly Limit",
                            "window": "weekly",
                            "remaining_fraction": 0.72,
                            "reset_time": default_weekly_reset,
                        },
                    ],
                },
                {
                    "name": "Gemini 4 Argon",
                    "buckets": [
                        {
                            "name": "5-Hour Limit",
                            "window": "5h",
                            "remaining_fraction": 0.92,
                            "reset_time": default_five_reset,
                        },
                        {
                            "name": "Weekly Limit",
                            "window": "weekly",
                            "remaining_fraction": 0.88,
                            "reset_time": default_weekly_reset,
                        },
                    ],
                },
                {
                    "name": "Gemini Models",
                    "buckets": [
                        {
                            "name": "5-Hour Limit",
                            "window": "5h",
                            "remaining_fraction": 0.96,
                            "reset_time": default_five_reset,
                        },
                        {
                            "name": "Weekly Limit",
                            "window": "weekly",
                            "remaining_fraction": 0.94,
                            "reset_time": default_weekly_reset,
                        },
                    ],
                },
            ]
        self.tokens_consumed_by_pool: dict[str, dict[str, int]] = {}

    def consume_tokens(
        self,
        model_name: str,
        work_tokens: int,
        overhead_tokens: int = 0,
    ) -> float:
        """Simulate directional token consumption against the calibrated pool capacity."""
        group = resolve_quota_group(model_name)
        total = max(0, work_tokens) + max(0, overhead_tokens)
        capacity = DEFAULT_POOL_CAPACITIES.get(group, 1_000_000)
        delta_pct = (total / capacity) * 100.0 if capacity > 0 else 0.0
        delta_fraction = delta_pct / 100.0

        counts = self.tokens_consumed_by_pool.setdefault(
            group, {"work_tokens": 0, "overhead_tokens": 0}
        )
        counts["work_tokens"] += max(0, work_tokens)
        counts["overhead_tokens"] += max(0, overhead_tokens)

        for entry in self._groups:
            if resolve_quota_group(str(entry.get("name", ""))) == group:
                for bucket in entry.get("buckets", []):
                    if "remaining_fraction" in bucket:
                        current = float(bucket["remaining_fraction"])
                        bucket["remaining_fraction"] = round(max(0.0, current - delta_fraction), 6)
        return round(delta_pct, 4)

    def get_snapshot(
        self,
        rolling_threshold: float = 15.0,
        weekly_threshold: float = 10.0,
    ) -> QuotaSnapshot:
        now_utc = datetime.now(UTC)
        payload = {"command": {"data": {"groups": self._groups}}}
        records = parse_usage_json(payload, recorded_at=now_utc)
        models = _build_models_from_records(
            records,
            rolling_threshold=rolling_threshold,
            weekly_threshold=weekly_threshold,
        )
        return QuotaSnapshot(records=records, models=models, recorded_at=now_utc)

    def get_model_quota(
        self,
        model_name: str,
        rolling_threshold: float = 15.0,
        weekly_threshold: float = 10.0,
        respect_shared_lock: bool = True,
    ) -> ModelQuota:
        group = resolve_quota_group(model_name)
        if respect_shared_lock:
            lock_details = get_shared_model_lock_details(group)
            if lock_details is not None:
                return _build_shared_lock_quota(model_name, group, lock_details)

        snapshot = self.get_snapshot(
            rolling_threshold=rolling_threshold,
            weekly_threshold=weekly_threshold,
        )
        pool_quota = snapshot.models.get(group)
        if pool_quota is None:
            raise KeyError(f"Quota pool {group!r} (for model {model_name!r}) not configured")
        return pool_quota.model_copy(update={"model_name": model_name})
