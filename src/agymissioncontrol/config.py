"""Configuration models, credit policies, and project-scoped path resolution."""

from __future__ import annotations

import os
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-3-7-sonnet": {
        "input_per_1m": 3.00,
        "output_per_1m": 15.00,
        "cached_input_per_1m": 0.30,
    },
    "gemini-2.5-pro": {
        "input_per_1m": 1.25,
        "output_per_1m": 10.00,
        "cached_input_per_1m": 0.31,
    },
    "gemini-2.5-flash": {
        "input_per_1m": 0.15,
        "output_per_1m": 0.60,
        "cached_input_per_1m": 0.04,
    },
}


class CreditPolicy(StrEnum):
    """Controls when purchased account credits may backstop depleted quota."""

    NEVER = "never"
    BUDGET_CAPPED = "budget_capped"
    ALWAYS = "always"


class CreditTrigger(StrEnum):
    """Controls which quota exhaustion tier triggers credit fallback."""

    WEEKLY_ONLY = "weekly_only"
    ANY = "any"


class ProjectConfig(BaseModel):
    """Project-level metadata."""

    name: str = "agymissioncontrol"
    default_branch: str = "main"


class ModelsConfig(BaseModel):
    """Model selection and token pricing table."""

    default_model: str = "claude-3-7-sonnet"
    fallback_model: str | None = "gemini-2.5-pro"
    pricing: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_PRICING.items()}
    )


class CreditConfig(BaseModel):
    """Credit fallback and budget protection settings."""

    policy: CreditPolicy = CreditPolicy.NEVER
    max_burn_usd: float = Field(default=0.00, ge=0.0)
    trigger: CreditTrigger = CreditTrigger.WEEKLY_ONLY
    on_cap_reached: Literal["sleep", "halt"] = "sleep"


class GovernorConfig(BaseModel):
    """Quota threshold, sleep buffer, and credit governor limits."""

    threshold_percent: float = Field(default=15.0, ge=0.0, le=100.0)
    weekly_threshold_percent: float = Field(default=10.0, ge=0.0, le=100.0)
    safety_buffer_seconds: int = Field(default=60, ge=0)
    max_sleep_seconds: int | None = Field(default=None, ge=0)
    max_session_tokens: int | None = Field(default=None, ge=1)
    credits: CreditConfig = Field(default_factory=CreditConfig)


class ExecutionConfig(BaseModel):
    """Execution harness settings."""

    manifest_file: str = "TASKS.md"
    max_retries: int = Field(default=3, ge=0)
    verify_command: str | None = None
    auto_sleep: bool = True


class ResolvedPaths(BaseModel):
    """Project-scoped filesystem locations isolated to a single target repository."""

    project_root: Path
    runtime_dir: Path
    manifest_path: Path
    logs_dir: Path
    reports_dir: Path
    db_path: Path


class AppConfig(BaseModel):
    """Root validated application configuration."""

    project: ProjectConfig = Field(default_factory=ProjectConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    governor: GovernorConfig = Field(default_factory=GovernorConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    paths: ResolvedPaths | None = None


def resolve_project_paths(
    project_dir: Path | None = None,
    manifest_override: str | None = None,
) -> ResolvedPaths:
    """Resolve all project-scoped runtime paths under `<project_root>/.agymissioncontrol/`."""
    root = (project_dir or Path.cwd()).resolve()
    runtime_dir = root / ".agymissioncontrol"
    manifest_name = manifest_override or "TASKS.md"
    return ResolvedPaths(
        project_root=root,
        runtime_dir=runtime_dir,
        manifest_path=(root / manifest_name).resolve(),
        logs_dir=runtime_dir / "logs",
        reports_dir=runtime_dir / "reports",
        db_path=runtime_dir / "missioncontrol.db",
    )


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> Path:
    """Atomically write `content` to `path` via a sibling temp file, fsync, and `os.replace`."""
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise
    return target


def ensure_gitignore_guard(project_root: Path) -> bool:
    """Ensure `.agymissioncontrol/` is ignored in `<project_root>/.gitignore`.

    Returns ``True`` if `.gitignore` was created or updated, or ``False`` if it
    already contained the entry.
    """
    gitignore_path = project_root.resolve() / ".gitignore"
    target_entries = {".agymissioncontrol", ".agymissioncontrol/", "/.agymissioncontrol/"}

    if gitignore_path.exists():
        content = gitignore_path.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if any(line in target_entries for line in lines):
            return False
        separator = "" if (not content or content.endswith("\n")) else "\n"
        atomic_write_text(
            gitignore_path,
            f"{content}{separator}.agymissioncontrol/\n",
            encoding="utf-8",
        )
        return True

    atomic_write_text(gitignore_path, ".agymissioncontrol/\n", encoding="utf-8")
    return True


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge nested dictionaries without mutating inputs."""
    merged = dict(base)
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parse_bool(raw: str | bool) -> bool:
    if isinstance(raw, bool):
        return raw
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {raw!r}")


def _env_overrides() -> dict[str, Any]:
    """Collect configuration overrides from `AGY_*` environment variables."""
    overrides: dict[str, Any] = {}
    env = os.environ

    def set_nested(section: str, key: str, val: Any, sub_section: str | None = None) -> None:
        target = overrides.setdefault(section, {})
        if sub_section is not None:
            target = target.setdefault(sub_section, {})
        target[key] = val

    if "AGY_DEFAULT_MODEL" in env:
        set_nested("models", "default_model", env["AGY_DEFAULT_MODEL"])
    if "AGY_FALLBACK_MODEL" in env:
        set_nested("models", "fallback_model", env["AGY_FALLBACK_MODEL"])

    if "AGY_THRESHOLD_PERCENT" in env:
        set_nested("governor", "threshold_percent", float(env["AGY_THRESHOLD_PERCENT"]))
    if "AGY_WEEKLY_THRESHOLD_PERCENT" in env:
        set_nested(
            "governor",
            "weekly_threshold_percent",
            float(env["AGY_WEEKLY_THRESHOLD_PERCENT"]),
        )
    if "AGY_SAFETY_BUFFER_SECONDS" in env:
        set_nested("governor", "safety_buffer_seconds", int(env["AGY_SAFETY_BUFFER_SECONDS"]))
    if "AGY_MAX_SLEEP_SECONDS" in env:
        set_nested("governor", "max_sleep_seconds", int(env["AGY_MAX_SLEEP_SECONDS"]))
    if "AGY_MAX_SESSION_TOKENS" in env:
        set_nested("governor", "max_session_tokens", int(env["AGY_MAX_SESSION_TOKENS"]))

    if "AGY_CREDIT_POLICY" in env:
        set_nested("governor", "policy", env["AGY_CREDIT_POLICY"], sub_section="credits")
    if "AGY_MAX_CREDIT_BURN" in env:
        set_nested(
            "governor",
            "max_burn_usd",
            float(env["AGY_MAX_CREDIT_BURN"]),
            sub_section="credits",
        )
    if "AGY_CREDIT_TRIGGER" in env:
        set_nested("governor", "trigger", env["AGY_CREDIT_TRIGGER"], sub_section="credits")
    if "AGY_CREDIT_ON_CAP_REACHED" in env:
        set_nested(
            "governor",
            "on_cap_reached",
            env["AGY_CREDIT_ON_CAP_REACHED"],
            sub_section="credits",
        )
    if "AGY_BYPASS_5H_QUOTA" in env and _parse_bool(env["AGY_BYPASS_5H_QUOTA"]):
        set_nested("governor", "trigger", CreditTrigger.ANY.value, sub_section="credits")

    if "AGY_MANIFEST_FILE" in env:
        set_nested("execution", "manifest_file", env["AGY_MANIFEST_FILE"])
    if "AGY_AUTO_SLEEP" in env:
        set_nested("execution", "auto_sleep", _parse_bool(env["AGY_AUTO_SLEEP"]))

    return overrides


def _normalize_cli_overrides(cli_overrides: dict[str, Any]) -> dict[str, Any]:
    """Normalize flat CLI flag dicts and nested dicts into `AppConfig` structure."""
    normalized: dict[str, Any] = {}
    nested_keys = {"project", "models", "governor", "execution"}

    flat_map: dict[str, tuple[str, ...]] = {
        "default_model": ("models", "default_model"),
        "model": ("models", "default_model"),
        "fallback_model": ("models", "fallback_model"),
        "threshold_percent": ("governor", "threshold_percent"),
        "threshold": ("governor", "threshold_percent"),
        "weekly_threshold_percent": ("governor", "weekly_threshold_percent"),
        "weekly_threshold": ("governor", "weekly_threshold_percent"),
        "safety_buffer_seconds": ("governor", "safety_buffer_seconds"),
        "max_sleep_seconds": ("governor", "max_sleep_seconds"),
        "max_sleep": ("governor", "max_sleep_seconds"),
        "max_session_tokens": ("governor", "max_session_tokens"),
        "credit_policy": ("governor", "credits", "policy"),
        "policy": ("governor", "credits", "policy"),
        "max_credit_burn": ("governor", "credits", "max_burn_usd"),
        "max_burn_usd": ("governor", "credits", "max_burn_usd"),
        "credit_trigger": ("governor", "credits", "trigger"),
        "trigger": ("governor", "credits", "trigger"),
        "on_cap_reached": ("governor", "credits", "on_cap_reached"),
        "manifest_file": ("execution", "manifest_file"),
        "max_retries": ("execution", "max_retries"),
        "verify_command": ("execution", "verify_command"),
        "auto_sleep": ("execution", "auto_sleep"),
    }

    for key, value in cli_overrides.items():
        if value is None:
            continue
        if key in nested_keys and isinstance(value, dict):
            normalized = _deep_merge(normalized, {key: value})
            continue
        if key == "bypass_5h_quota":
            if value:
                normalized = _deep_merge(
                    normalized, {"governor": {"credits": {"trigger": CreditTrigger.ANY.value}}}
                )
            continue
        path = flat_map.get(key)
        if path is None:
            continue
        cursor = normalized
        for segment in path[:-1]:
            cursor = cursor.setdefault(segment, {})
        cursor[path[-1]] = value.value if isinstance(value, StrEnum) else value

    return normalized


def load_config(
    config_path: Path | None = None,
    project_dir: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
    ensure_gitignore: bool = False,
) -> AppConfig:
    """Load, merge, and validate configuration.

    Precedence order: ``CLI Arguments > Environment Variables > Project YAML > Safe Defaults``.
    """
    if project_dir is not None:
        root = project_dir.resolve()
    elif config_path is not None:
        root = config_path.resolve().parent
    else:
        root = Path.cwd().resolve()

    yaml_target = (
        config_path.resolve() if config_path is not None else root / ".agymissioncontrol.yaml"
    )
    raw_data: dict[str, Any] = {}
    if yaml_target.exists():
        loaded = yaml.safe_load(yaml_target.read_text(encoding="utf-8"))
        if loaded is not None:
            if not isinstance(loaded, dict):
                raise ValueError(f"Expected mapping in configuration file: {yaml_target}")
            raw_data = loaded

    env_data = _env_overrides()
    merged = _deep_merge(raw_data, env_data)

    if cli_overrides:
        merged = _deep_merge(merged, _normalize_cli_overrides(cli_overrides))

    config = AppConfig.model_validate(merged)
    config.paths = resolve_project_paths(
        project_dir=root,
        manifest_override=config.execution.manifest_file,
    )

    if ensure_gitignore:
        ensure_gitignore_guard(root)

    return config
