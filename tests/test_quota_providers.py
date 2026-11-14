"""Deep verification unit tests for quota telemetry models, locks, and providers."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agymissioncontrol.quota import (
    AgyCliProvider,
    MockProvider,
    UsageTelemetry,
    calculate_cost_usd,
    check_shared_model_lock,
    clear_shared_model_lock,
    estimate_quota_impact,
    fetch_usage_json,
    get_registry_db_path,
    parse_usage_json,
    resolve_quota_group,
    set_shared_model_lock,
)


def test_resolve_quota_group_canonical_mappings() -> None:
    assert resolve_quota_group("claude-3-7-sonnet") == "Claude and GPT models"
    assert resolve_quota_group("claude-opus-4") == "Claude and GPT models"
    assert resolve_quota_group("gpt-oss-120b") == "Claude and GPT models"
    assert resolve_quota_group("model-3p-haiku") == "Claude and GPT models"
    assert resolve_quota_group("Claude and GPT models") == "Claude and GPT models"

    assert resolve_quota_group("gemini-4-argon") == "Gemini 4 Argon"
    assert resolve_quota_group("orchestrator") == "Gemini 4 Argon"
    assert resolve_quota_group("Gemini 4 Argon") == "Gemini 4 Argon"

    assert resolve_quota_group("gemini-2.5-pro") == "Gemini Models"
    assert resolve_quota_group("gemini-2.5-flash") == "Gemini Models"
    assert resolve_quota_group("Gemini Models") == "Gemini Models"


def test_parse_usage_json_official_groups_and_models_fallback() -> None:
    official_payload = {
        "command": {
            "data": {
                "groups": [
                    {
                        "name": "Claude and GPT models",
                        "buckets": [
                            {
                                "name": "5-Hour Limit",
                                "window": "5h",
                                "remaining_fraction": 0.88256,
                                "reset_time": "2026-11-11T10:12:48Z",
                            },
                            {
                                "name": "Weekly Limit",
                                "window": "weekly",
                                "remaining_fraction": 0.72,
                                "reset_time": "2026-11-18T00:00:00+00:00",
                            },
                        ],
                    },
                    {
                        "name": "Gemini 4 Argon",
                        "buckets": [
                            {
                                "name": "5-Hour Limit",
                                "window": "5h",
                                "remaining_fraction": 0.95,
                                "reset_time": "2026-11-11T11:00:00Z",
                            }
                        ],
                    },
                ]
            }
        }
    }
    recorded_at = datetime(2026, 11, 11, 8, 0, 0, tzinfo=UTC)
    records = parse_usage_json(official_payload, recorded_at=recorded_at)
    assert len(records) == 3

    claude_5h = next(
        r
        for r in records
        if r.model_family == "Claude and GPT models" and r.limit_type == "five_hour"
    )
    assert claude_5h.remaining_pct == 88.26
    assert claude_5h.reset_at == datetime(2026, 11, 11, 10, 12, 48, tzinfo=UTC)
    assert claude_5h.recorded_at == recorded_at
    assert "Claude and GPT models" in claude_5h.raw_line

    # Secondary fallback format (`models` mapping)
    fallback_payload = {
        "models": {
            "Gemini Models": {
                "five_hour": {"remaining_pct": 95, "reset_at": "2026-11-11T11:00:00Z"},
            }
        }
    }
    fallback_records = parse_usage_json(fallback_payload, recorded_at=recorded_at)
    assert len(fallback_records) == 1
    assert fallback_records[0].remaining_pct == 95.0


@pytest.mark.parametrize(
    "bad_payload",
    [
        "not-a-dict",
        {},
        {"command": "not-a-dict"},
        {"command": {"data": {"groups": "not-a-list"}}},
        {
            "command": {
                "data": {
                    "groups": [
                        {
                            "name": "Claude and GPT models",
                            "buckets": [
                                {
                                    "window": "5h",
                                    "remaining_fraction": 1.5,
                                    "reset_time": "2026-11-11T10:00:00Z",
                                }
                            ],
                        }
                    ]
                }
            }
        },
        {
            "command": {
                "data": {
                    "groups": [
                        {
                            "name": "Gemini Models",
                            "buckets": [
                                {
                                    "window": "bad_window",
                                    "remaining_fraction": 0.5,
                                    "reset_time": "2026-11-11T10:00:00Z",
                                }
                            ],
                        }
                    ]
                }
            }
        },
        {"models": "not-an-object"},
        {"models": {"Claude and GPT models": {"five_hour": {"reset_at": "2026-11-11T10:00:00Z"}}}},
        {"models": {"Gemini Models": {"weekly": {"remaining_pct": 50.0, "reset_at": "invalid"}}}},
    ],
)
def test_parse_usage_json_rejects_malformed_payloads(bad_payload: object) -> None:
    with pytest.raises(ValueError):
        parse_usage_json(bad_payload)  # type: ignore[arg-type]


def test_fetch_usage_json_handles_success_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_kwargs: dict[str, object] = {}

    def fake_run_ok(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        seen_kwargs["cmd"] = cmd
        seen_kwargs.update(kwargs)
        return SimpleNamespace(stdout=json.dumps({"models": {}}), stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run_ok)
    assert fetch_usage_json() == {"models": {}}
    assert seen_kwargs["cmd"] == ["agy", "-p", "/usage", "--output-format", "json"]
    assert seen_kwargs["stdin"] is subprocess.DEVNULL
    assert seen_kwargs["timeout"] == 30

    def fake_run_bad_json(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(stdout="<html>not json</html>", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run_bad_json)
    with pytest.raises(ValueError, match="Malformed JSON"):
        fetch_usage_json()

    def fake_run_timeout(*args: object, **kwargs: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(cmd="agy", timeout=30)

    monkeypatch.setattr(subprocess, "run", fake_run_timeout)
    with pytest.raises(RuntimeError, match="Timed out"):
        fetch_usage_json()

    def fake_run_nonzero(*args: object, **kwargs: object) -> SimpleNamespace:
        raise subprocess.CalledProcessError(returncode=1, cmd="agy", stderr="auth error")

    monkeypatch.setattr(subprocess, "run", fake_run_nonzero)
    with pytest.raises(RuntimeError, match="exited with 1"):
        fetch_usage_json()


def test_usage_telemetry_overhead_cache_and_cost_math() -> None:
    empty = UsageTelemetry()
    assert empty.total_tokens == 0
    assert empty.overhead_pct == 0.0
    assert empty.cache_hit_rate == 0.0
    assert calculate_cost_usd(empty, "claude-3-7-sonnet") == 0.0

    usage = UsageTelemetry(
        prompt_tokens=800_000,
        cache_read_tokens=200_000,
        candidate_tokens=100_000,
        thinking_tokens=50_000,
        work_tokens=900_000,
        overhead_tokens=100_000,
    )
    assert usage.total_tokens == 1_000_000
    assert usage.overhead_pct == 10.0
    assert usage.cache_hit_rate == 20.0
    # 0.8 * $3.00 + 0.15 * $15.00 + 0.2 * $0.30 = 2.40 + 2.25 + 0.06 = 4.71
    assert calculate_cost_usd(usage, "claude-3-7-sonnet") == 4.71

    impact = estimate_quota_impact(
        work_tokens=90_000, overhead_tokens=10_000, model="claude-3-7-sonnet"
    )
    assert impact.estimated_tokens == 100_000
    assert impact.directional_allowance_pct == 10.0
    assert impact.estimated_cost_usd == 0.3


def test_shared_model_locks_cross_pool_and_wal_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))

    future = datetime.now(UTC) + timedelta(minutes=20)
    set_shared_model_lock("claude-3-7-sonnet", future, reason="429_RATE_LIMIT")

    # Sibling models inside the same canonical pool see the lock immediately
    assert check_shared_model_lock("claude-opus") == future
    assert check_shared_model_lock("gpt-oss") == future
    assert check_shared_model_lock("Claude and GPT models") == future
    # Unrelated pool remains unlocked
    assert check_shared_model_lock("gemini-2.5-pro") is None

    # Expired lock is ignored cleanly
    past = datetime.now(UTC) - timedelta(seconds=5)
    set_shared_model_lock("gemini-2.5-pro", past, reason="EXPIRED")
    assert check_shared_model_lock("gemini-2.5-flash") is None

    # Clearing removes the canonical pool lock
    clear_shared_model_lock("claude-3-7-sonnet")
    assert check_shared_model_lock("claude-opus") is None

    # Registry DB uses WAL mode and user_version = 1
    conn = sqlite3.connect(str(get_registry_db_path()))
    try:
        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        version = conn.execute("PRAGMA user_version;").fetchone()[0]
        assert str(journal).lower() == "wal"
        assert version == 1
    finally:
        conn.close()


def test_agy_cli_provider_persists_snapshots_and_respects_shared_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    proj_dir = tmp_path / "repo-a"
    proj_dir.mkdir()

    reset_5h = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    reset_wk = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    calls = {"count": 0}

    def fake_fetcher() -> dict[str, object]:
        calls["count"] += 1
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
                                    "remaining_fraction": 0.12,
                                    "reset_time": reset_5h,
                                },
                                {
                                    "name": "Weekly Limit",
                                    "window": "weekly",
                                    "remaining_fraction": 0.55,
                                    "reset_time": reset_wk,
                                },
                            ],
                        },
                        {
                            "name": "Gemini Models",
                            "buckets": [
                                {
                                    "name": "5-Hour Limit",
                                    "window": "5h",
                                    "remaining_fraction": 0.90,
                                    "reset_time": reset_5h,
                                },
                                {
                                    "name": "Weekly Limit",
                                    "window": "weekly",
                                    "remaining_fraction": 0.95,
                                    "reset_time": reset_wk,
                                },
                            ],
                        },
                    ]
                }
            }
        }

    provider = AgyCliProvider(project_dir=proj_dir, fetcher=fake_fetcher)
    snapshot = provider.get_snapshot(rolling_threshold=15.0, weekly_threshold=10.0)
    assert calls["count"] == 1
    assert len(snapshot.records) == 4
    claude_q = snapshot.models["Claude and GPT models"]
    assert claude_q.is_rolling_depleted is True
    assert claude_q.is_weekly_depleted is False

    # Verify rows persisted into both registry.db and project missioncontrol.db
    for db_file in (
        get_registry_db_path(),
        proj_dir / ".agymissioncontrol" / "missioncontrol.db",
    ):
        conn = sqlite3.connect(str(db_file))
        try:
            count = conn.execute("SELECT COUNT(*) FROM quota_snapshots").fetchone()[0]
            assert count == 4
        finally:
            conn.close()

    # Now set a shared lock on Claude pool; get_model_quota short-circuits subprocess fetcher
    lock_until = datetime.now(UTC) + timedelta(minutes=30)
    set_shared_model_lock("claude-opus", lock_until, reason="429_BURST")
    locked_q = provider.get_model_quota("claude-3-7-sonnet")
    assert calls["count"] == 1  # fetcher was NOT called again
    assert locked_q.is_locked_out is True
    assert locked_q.locked_by_reason == "429_BURST"
    assert locked_q.rolling_reset_epoch == lock_until


def test_mock_provider_simulation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    provider = MockProvider()
    delta = provider.consume_tokens(
        "claude-3-7-sonnet", work_tokens=700_000, overhead_tokens=50_000
    )
    assert delta == 75.0

    quota = provider.get_model_quota("claude-3-7-sonnet", rolling_threshold=15.0)
    assert quota.rolling_window_remaining_pct == 10.0
    assert quota.is_rolling_depleted is True
