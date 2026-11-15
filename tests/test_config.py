"""Unit tests for configuration models, credit policy, and project path isolation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agymissioncontrol.config import (
    AppConfig,
    CreditConfig,
    CreditPolicy,
    CreditTrigger,
    ensure_gitignore_guard,
    load_config,
    resolve_project_paths,
)


def test_resolve_project_paths_isolated_across_projects(tmp_path: Path) -> None:
    proj_a = tmp_path / "project-a"
    proj_b = tmp_path / "project-b"
    proj_a.mkdir()
    proj_b.mkdir()

    paths_a = resolve_project_paths(project_dir=proj_a)
    paths_b = resolve_project_paths(project_dir=proj_b, manifest_override="ROADMAP.md")

    assert paths_a.project_root == proj_a.resolve()
    assert paths_b.project_root == proj_b.resolve()
    assert paths_a.runtime_dir == proj_a.resolve() / ".agymissioncontrol"
    assert paths_b.runtime_dir == proj_b.resolve() / ".agymissioncontrol"
    assert paths_a.db_path != paths_b.db_path
    assert paths_a.logs_dir != paths_b.logs_dir
    assert paths_a.reports_dir != paths_b.reports_dir
    assert paths_a.manifest_path == proj_a.resolve() / "TASKS.md"
    assert paths_b.manifest_path == proj_b.resolve() / "ROADMAP.md"


def test_ensure_gitignore_guard_create_append_and_idempotent(tmp_path: Path) -> None:
    # Creates .gitignore when missing
    created = ensure_gitignore_guard(tmp_path)
    assert created is True
    gitignore = tmp_path / ".gitignore"
    assert gitignore.read_text(encoding="utf-8") == ".agymissioncontrol/\n"

    # Idempotent on subsequent call
    second = ensure_gitignore_guard(tmp_path)
    assert second is False
    assert gitignore.read_text(encoding="utf-8") == ".agymissioncontrol/\n"

    # Appends cleanly when .gitignore exists without trailing newline
    gitignore.write_text("node_modules/\n*.pyc", encoding="utf-8")
    appended = ensure_gitignore_guard(tmp_path)
    assert appended is True
    assert gitignore.read_text(encoding="utf-8") == "node_modules/\n*.pyc\n.agymissioncontrol/\n"

    # Does not duplicate when already listed without trailing slash
    gitignore.write_text(".agymissioncontrol\n", encoding="utf-8")
    assert ensure_gitignore_guard(tmp_path) is False


def test_load_config_safe_defaults_without_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in (
        "AGY_DEFAULT_MODEL",
        "AGY_THRESHOLD_PERCENT",
        "AGY_WEEKLY_THRESHOLD_PERCENT",
        "AGY_SAFETY_BUFFER_SECONDS",
        "AGY_MAX_SLEEP_SECONDS",
        "AGY_MAX_SESSION_TOKENS",
        "AGY_CREDIT_POLICY",
        "AGY_MAX_CREDIT_BURN",
        "AGY_CREDIT_TRIGGER",
        "AGY_CREDIT_ON_CAP_REACHED",
        "AGY_BYPASS_5H_QUOTA",
    ):
        monkeypatch.delenv(var, raising=False)

    cfg = load_config(project_dir=tmp_path)
    assert isinstance(cfg, AppConfig)
    assert cfg.paths is not None
    assert cfg.paths.project_root == tmp_path.resolve()
    assert cfg.paths.manifest_path == tmp_path.resolve() / "TASKS.md"
    assert cfg.paths.db_path == tmp_path.resolve() / ".agymissioncontrol" / "missioncontrol.db"
    assert cfg.paths.logs_dir == tmp_path.resolve() / ".agymissioncontrol" / "logs"
    assert cfg.paths.reports_dir == tmp_path.resolve() / ".agymissioncontrol" / "reports"
    assert not (tmp_path / ".gitignore").exists()


def test_load_config_precedence_cli_over_env_over_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yaml_path = tmp_path / ".agymissioncontrol.yaml"
    yaml_path.write_text(
        """
project:
  name: custom-repo
models:
  default_model: gemini-2.5-pro
governor:
  threshold_percent: 20.0
  weekly_threshold_percent: 12.0
  credits:
    policy: budget_capped
    max_burn_usd: 5.0
    trigger: weekly_only
execution:
  manifest_file: CUSTOM_TASKS.md
""".lstrip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("AGY_THRESHOLD_PERCENT", "18.0")
    monkeypatch.setenv("AGY_MAX_CREDIT_BURN", "12.50")
    monkeypatch.setenv("AGY_CREDIT_POLICY", "always")

    cfg_env = load_config(project_dir=tmp_path)
    assert cfg_env.project.name == "custom-repo"
    assert cfg_env.models.default_model == "gemini-2.5-pro"
    assert cfg_env.governor.threshold_percent == 18.0
    assert cfg_env.governor.weekly_threshold_percent == 12.0
    assert cfg_env.governor.credits.policy == CreditPolicy.ALWAYS
    assert cfg_env.governor.credits.max_burn_usd == 12.50
    assert cfg_env.paths is not None
    assert cfg_env.paths.manifest_path == (tmp_path / "CUSTOM_TASKS.md").resolve()

    # CLI overrides win over both Env and YAML
    cfg_cli = load_config(
        project_dir=tmp_path,
        cli_overrides={
            "threshold": 8.0,
            "credit_policy": CreditPolicy.BUDGET_CAPPED,
            "max_credit_burn": 25.0,
            "bypass_5h_quota": True,
            "auto_sleep": False,
        },
        ensure_gitignore=True,
    )
    assert cfg_cli.governor.threshold_percent == 8.0
    assert cfg_cli.governor.credits.policy == CreditPolicy.BUDGET_CAPPED
    assert cfg_cli.governor.credits.max_burn_usd == 25.0
    assert cfg_cli.governor.credits.trigger == CreditTrigger.ANY
    assert cfg_cli.execution.auto_sleep is False
    assert (tmp_path / ".gitignore").exists()


def test_credit_config_validation_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        CreditConfig(max_burn_usd=-1.0)

    with pytest.raises(ValidationError):
        CreditConfig.model_validate({"policy": "unlimited"})

    with pytest.raises(ValidationError):
        CreditConfig.model_validate({"on_cap_reached": "panic"})


def test_load_config_malformed_yaml_missing_path_and_invalid_env_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Missing explicit config_path falls back cleanly while anchoring project_root to its parent
    nested = tmp_path / "subdir"
    nested.mkdir()
    missing_cfg = nested / "nonexistent.yaml"
    cfg_missing = load_config(config_path=missing_cfg)
    assert cfg_missing.paths is not None
    assert cfg_missing.paths.project_root == nested.resolve()

    # Non-mapping YAML root raises ValueError
    list_yaml = tmp_path / "list.yaml"
    list_yaml.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Expected mapping"):
        load_config(config_path=list_yaml)

    # Malformed YAML syntax propagates yaml.YAMLError
    bad_yaml = tmp_path / "broken.yaml"
    bad_yaml.write_text("governor: [unclosed\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_config(config_path=bad_yaml)

    # Invalid numeric env overrides raise Pydantic ValidationError uniformly (QW-03)
    monkeypatch.setenv("AGY_THRESHOLD_PERCENT", "not-a-float")
    with pytest.raises(ValidationError):
        load_config(project_dir=tmp_path)
    monkeypatch.delenv("AGY_THRESHOLD_PERCENT")

    monkeypatch.setenv("AGY_MAX_SLEEP_SECONDS", "abc")
    with pytest.raises(ValidationError):
        load_config(project_dir=tmp_path)
    monkeypatch.delenv("AGY_MAX_SLEEP_SECONDS")

    monkeypatch.setenv("AGY_MAX_SESSION_TOKENS", "0")
    with pytest.raises(ValidationError):
        load_config(project_dir=tmp_path)
