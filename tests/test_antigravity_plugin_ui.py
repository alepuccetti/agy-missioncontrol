"""Tests for Antigravity plugin display name, Orchestrator skill, and IDE status pill."""

from __future__ import annotations

import importlib.util
import json
import os
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from agymissioncontrol.quota.ledger import record_quota_snapshots
from agymissioncontrol.quota.locks import set_shared_model_lock
from agymissioncontrol.quota.models import QuotaSnapshotRecord
from agymissioncontrol.registry import SessionState, SessionStatus, init_registry, register_session

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugin"
PACKAGE_SCRIPT = REPO_ROOT / "scripts" / "package_plugin.py"
SKILL_MD = PLUGIN_DIR / "skills" / "agymissioncontrol" / "SKILL.md"
WIDGET_SCRIPT = PLUGIN_DIR / "skills" / "agymissioncontrol" / "scripts" / "ui_widget.py"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_widget_module() -> ModuleType:
    return _load_module("ui_widget", WIDGET_SCRIPT)


def test_plugin_manifest_sets_mission_control_display_name() -> None:
    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "agymissioncontrol"
    assert manifest["displayName"] == "Mission Control"


def test_skill_markdown_frontmatter_and_command_guidance() -> None:
    content = SKILL_MD.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    _, frontmatter_raw, body = content.split("---\n", 2)
    frontmatter = yaml.safe_load(frontmatter_raw)
    assert frontmatter["name"] == "agymissioncontrol"
    assert isinstance(frontmatter["description"], str) and frontmatter["description"].strip()

    for expected_token in (
        "agymissioncontrol status --json",
        "agymissioncontrol gate --model",
        "agymissioncontrol report",
        "agymissioncontrol locks",
        "agymissioncontrol unlock",
        "agymissioncontrol plan",
        "/missioncontrol-status",
        "/mc",
        "/mc-status",
        "http://localhost:8484",
    ):
        assert expected_token in body


def test_generate_ide_status_pill_counts_and_naive_aware_datetimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widget = _load_widget_module()
    now_utc = datetime.now(UTC)

    monkeypatch.setattr(widget, "list_active_sessions", lambda: [])
    assert (
        widget.generate_ide_status_pill()
        == "[💤 Mission Control: All Gates Clear](http://localhost:8484)"
    )

    naive_wake = (now_utc + timedelta(minutes=12, seconds=30)).replace(tzinfo=None)
    single_sleeping = [
        SessionState(
            pid=101,
            project_name="repo-one",
            project_dir="/tmp/repo-one",
            label="gate-1",
            model="claude-sonnet-4-6",
            quota_group="Claude and GPT models",
            state=SessionStatus.SLEEPING,
            sleep_started_at=now_utc,
            wakes_at=naive_wake,
            credit_burn_usd=0.0,
            updated_at=now_utc,
        ),
        SessionState(
            pid=102,
            project_name="repo-two",
            project_dir="/tmp/repo-two",
            label="active-1",
            model="gemini-4-pro",
            quota_group="Gemini Models",
            state=SessionStatus.ACTIVE,
            credit_burn_usd=0.0,
            updated_at=now_utc,
        ),
    ]
    monkeypatch.setattr(widget, "list_active_sessions", lambda: single_sleeping)
    single_pill = widget.generate_ide_status_pill()
    assert single_pill.startswith("[💤 1 Gate Sleeping (")
    assert "12m left)](http://localhost:8484)" in single_pill

    multi_sleeping = [
        SessionState(
            pid=201,
            project_name="repo-a",
            project_dir="/tmp/a",
            label="early",
            model="claude-sonnet-4-6",
            quota_group="Claude and GPT models",
            state=SessionStatus.SLEEPING,
            wakes_at=now_utc + timedelta(minutes=6, seconds=20),
            updated_at=now_utc,
        ),
        SessionState(
            pid=202,
            project_name="repo-b",
            project_dir="/tmp/b",
            label="later",
            model="gemini-4-pro",
            quota_group="Gemini Models",
            state=SessionStatus.SLEEPING,
            wakes_at=now_utc + timedelta(minutes=45),
            updated_at=now_utc,
        ),
    ]
    monkeypatch.setattr(widget, "list_active_sessions", lambda: multi_sleeping)
    multi_pill = widget.generate_ide_status_pill(dashboard_url="http://localhost:9000")
    assert multi_pill.startswith("[💤 2 Gates Sleeping (")
    assert "6m left)](http://localhost:9000)" in multi_pill


def test_render_quota_status_markdown_includes_pools_locks_and_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGY_MC_STATE_DIR", str(tmp_path / "state"))
    init_registry()
    now = datetime.now(UTC)

    record_quota_snapshots(
        [
            QuotaSnapshotRecord(
                model_family="Claude and GPT models",
                limit_type="five_hour",
                remaining_pct=0.0,
                reset_at=now + timedelta(hours=1),
                recorded_at=now,
            ),
            QuotaSnapshotRecord(
                model_family="Claude and GPT models",
                limit_type="weekly",
                remaining_pct=75.0,
                reset_at=now + timedelta(days=4),
                recorded_at=now,
            ),
        ]
    )
    set_shared_model_lock(
        "claude-sonnet-4-6",
        locked_until=now + timedelta(minutes=30),
        reason="FIVE_HOUR_LOCK",
    )
    register_session(
        SessionState(
            pid=os.getpid(),
            project_name="mission-ctrl",
            project_dir=str(tmp_path),
            label="e04-t05",
            model="claude-sonnet-4-6",
            quota_group="Claude and GPT models",
            state=SessionStatus.SLEEPING,
            sleep_started_at=now,
            wakes_at=now + timedelta(minutes=15, seconds=20),
            credit_burn_usd=2.50,
            updated_at=now,
        )
    )

    widget = _load_widget_module()
    output = widget.render_quota_status_markdown()
    assert "[💤 1 Gate Sleeping (" in output
    assert "http://localhost:8484" in output
    assert "Claude and GPT models" in output
    assert "LOCKED" in output
    assert "FIVE_HOUR_LOCK" in output
    assert "mission-ctrl" in output
    assert "e04-t05" in output
    assert "$2.50" in output


def test_package_plugin_includes_skill_widget_and_display_name(tmp_path: Path) -> None:
    package_mod = _load_module("package_plugin", PACKAGE_SCRIPT)
    archive = package_mod.package_plugin(PLUGIN_DIR, tmp_path / "dist", "v1.0.0")
    assert archive.is_file()
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
        assert "agymissioncontrol-plugin/skills/agymissioncontrol/SKILL.md" in names
        assert "agymissioncontrol-plugin/skills/agymissioncontrol/scripts/ui_widget.py" in names
        manifest_member = tar.extractfile("agymissioncontrol-plugin/plugin.json")
        assert manifest_member is not None
        packaged_manifest = json.loads(manifest_member.read().decode("utf-8"))
        assert packaged_manifest["displayName"] == "Mission Control"
        assert packaged_manifest["version"] == "1.0.0"
