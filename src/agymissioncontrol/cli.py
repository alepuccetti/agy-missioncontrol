"""Typer CLI entrypoint for agyMissionControl."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.tree import Tree

import agymissioncontrol
from agymissioncontrol.config import CreditPolicy, CreditTrigger, load_config
from agymissioncontrol.manifest import (
    CyclicDependencyError,
    DependencyResolver,
    GitHubTracker,
    GitLabTracker,
    IssueTracker,
    MockTracker,
    RawIssue,
    TaskItem,
    TrackerError,
    extract_task_spec,
    merge_manifests,
    parse_manifest_document,
    sanitize_issue_body,
    serialize_manifest,
)
from agymissioncontrol.quota import (
    AgyCliProvider,
    MockProvider,
    QuotaProvider,
    evaluate_and_gate,
    get_credit_spend,
    get_session_token_totals,
)
from agymissioncontrol.ui.display import build_status_payload, render_status_table

app = typer.Typer(
    name="agymissioncontrol",
    help="Autonomous task orchestrator, quota governor, and issue-to-spec compiler for agy.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(agymissioncontrol.__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the agymissioncontrol version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """agyMissionControl CLI root callback."""
    _ = version


def _build_provider(project_dir: Path, mock: bool) -> QuotaProvider:
    if mock or os.environ.get("AGY_MC_MOCK_PROVIDER") == "1":
        return MockProvider(project_dir=project_dir)
    return AgyCliProvider(project_dir=project_dir)


@app.command()
def status(
    project_dir: Annotated[
        Path | None,
        typer.Option(
            "--project-dir",
            help="Target project directory (defaults to current working directory).",
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="Path to explicit .agymissioncontrol.yaml configuration file.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit machine-readable JSON dual-tier quota and credit telemetry.",
        ),
    ] = False,
    mock: Annotated[
        bool,
        typer.Option(
            "--mock",
            help="Use deterministic mock telemetry instead of invoking external `agy` CLI.",
        ),
    ] = False,
) -> None:
    """Display live 5-hour rolling quota, weekly ceiling, shared locks, and credit spend."""
    cfg = load_config(config_path=config, project_dir=project_dir)
    assert cfg.paths is not None
    root = cfg.paths.project_root
    provider = _build_provider(root, mock=mock)

    try:
        snapshot = provider.get_snapshot(
            rolling_threshold=cfg.governor.threshold_percent,
            weekly_threshold=cfg.governor.weekly_threshold_percent,
        )
    except (RuntimeError, ValueError) as exc:
        typer.echo(f"Error querying quota telemetry: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    credit_spend = get_credit_spend(root)
    session_usage = get_session_token_totals(root)

    if json_output:
        payload = build_status_payload(
            snapshot=snapshot,
            config=cfg,
            credit_spend_usd=credit_spend,
            session_usage=session_usage,
        )
        typer.echo(json.dumps(payload, indent=2))
        return

    table = render_status_table(
        snapshot=snapshot,
        config=cfg,
        credit_spend_usd=credit_spend,
        session_usage=session_usage,
    )
    Console().print(table)


@app.command()
def gate(
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="Model ID or quota group name to gate against.",
        ),
    ] = None,
    threshold: Annotated[
        float | None,
        typer.Option(
            "--threshold",
            help="Minimum 5-hour rolling quota % remaining (default: 15.0).",
        ),
    ] = None,
    weekly_threshold: Annotated[
        float | None,
        typer.Option(
            "--weekly-threshold",
            help="Minimum weekly quota % remaining (default: 10.0).",
        ),
    ] = None,
    auto_sleep: Annotated[
        bool | None,
        typer.Option(
            "--auto-sleep/--no-auto-sleep",
            help="Block in zero-token sleep until recovery (default: enabled).",
        ),
    ] = None,
    max_sleep: Annotated[
        int | None,
        typer.Option(
            "--max-sleep",
            help="Maximum sleep duration allowed in seconds before returning LOW_QUOTA.",
        ),
    ] = None,
    credit_policy: Annotated[
        CreditPolicy | None,
        typer.Option(
            "--credit-policy",
            help="Override AI credit fallback policy (never | budget_capped | always).",
        ),
    ] = None,
    max_credit_burn: Annotated[
        float | None,
        typer.Option(
            "--max-credit-burn",
            help="Override cumulative AI credit spend cap in USD.",
        ),
    ] = None,
    credit_trigger: Annotated[
        CreditTrigger | None,
        typer.Option(
            "--credit-trigger",
            help="Override when AI credits trigger (weekly_only | any).",
        ),
    ] = None,
    bypass_5h_quota: Annotated[
        bool,
        typer.Option(
            "--bypass-5h-quota",
            help="Bypass 5-hour rolling sleep via credits when credit policy permits.",
        ),
    ] = False,
    project_dir: Annotated[
        Path | None,
        typer.Option(
            "--project-dir",
            help="Target project directory (defaults to current working directory).",
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="Path to explicit .agymissioncontrol.yaml configuration file.",
        ),
    ] = None,
    mock: Annotated[
        bool,
        typer.Option(
            "--mock",
            help="Use deterministic mock telemetry instead of invoking external `agy` CLI.",
        ),
    ] = False,
) -> None:
    """Enforce pre-flight dual-tier quota and shared lockout gates."""
    cfg = load_config(
        config_path=config,
        project_dir=project_dir,
        cli_overrides={
            "model": model,
            "threshold": threshold,
            "weekly_threshold": weekly_threshold,
            "auto_sleep": auto_sleep,
            "max_sleep": max_sleep,
            "credit_policy": credit_policy,
            "max_credit_burn": max_credit_burn,
            "credit_trigger": credit_trigger,
            "bypass_5h_quota": bypass_5h_quota,
        },
    )
    assert cfg.paths is not None
    root = cfg.paths.project_root
    provider = _build_provider(root, mock=mock)

    try:
        decision = evaluate_and_gate(
            model=cfg.models.default_model,
            provider=provider,
            credit_cfg=cfg.governor.credits,
            rolling_threshold=cfg.governor.threshold_percent,
            weekly_threshold=cfg.governor.weekly_threshold_percent,
            safety_buffer_seconds=cfg.governor.safety_buffer_seconds,
            auto_sleep=cfg.execution.auto_sleep,
            max_sleep_seconds=cfg.governor.max_sleep_seconds,
            caller_bypass_5h=bypass_5h_quota,
            project_dir=root,
        )
    except (RuntimeError, ValueError) as exc:
        typer.echo(f"Error evaluating quota gate: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if decision.exit_code == 0:
        if decision.is_credit_funded:
            typer.echo(f"GO (CREDIT): {decision.reason}")
        else:
            typer.echo(f"GO (FREE): {decision.reason}")
    elif decision.exit_code == 1:
        typer.echo(f"LOW_QUOTA: {decision.reason}")
    else:
        typer.echo(f"HARD_LOCKOUT: {decision.reason}")

    raise typer.Exit(code=decision.exit_code)


def _fetch_issues_for_plan(
    *,
    provider: str,
    fixture: Path | None,
    milestone: str | None,
    labels: list[str] | None,
    issue: int | None,
) -> list[RawIssue]:
    normalized = provider.strip().lower()
    if normalized not in {"auto", "github", "gh", "gitlab", "glab", "mock"}:
        typer.echo(
            f"Unsupported tracker provider {provider!r}. Choose auto, github, gitlab, or mock.",
            err=True,
        )
        raise typer.Exit(code=1)

    if fixture is not None or normalized == "mock" or os.environ.get("AGY_MC_MOCK_PROVIDER") == "1":
        tracker: IssueTracker = MockTracker(fixture)
        return tracker.fetch_issues(milestone=milestone, labels=labels, issue_number=issue)

    if normalized in {"gitlab", "glab"}:
        return GitLabTracker().fetch_issues(milestone=milestone, labels=labels, issue_number=issue)

    if normalized in {"github", "gh"}:
        return GitHubTracker().fetch_issues(milestone=milestone, labels=labels, issue_number=issue)

    # `auto` tries GitHub first and falls back to offline mock fixtures if `gh` is unavailable.
    try:
        return GitHubTracker().fetch_issues(milestone=milestone, labels=labels, issue_number=issue)
    except TrackerError:
        return MockTracker(fixture).fetch_issues(
            milestone=milestone, labels=labels, issue_number=issue
        )


def _render_plan_tree(tasks: list[TaskItem], console: Console) -> None:
    tree = Tree(f"Task Execution Plan ({len(tasks)} tasks):")
    for idx, task in enumerate(tasks, start=1):
        dep_note = f"Depends on: {', '.join(task.depends_on)}" if task.depends_on else "Independent"
        tree.add(f"{idx}. {task.task_id}: {task.title} ({dep_note})")
    console.print(tree)


@app.command()
def plan(
    issue: Annotated[
        int | None,
        typer.Option("--issue", help="Compile a single issue number into the task manifest."),
    ] = None,
    provider: Annotated[
        str,
        typer.Option(
            "--provider",
            help="Issue tracker provider to query: auto, github, gitlab, or mock.",
        ),
    ] = "auto",
    milestone: Annotated[
        str | None,
        typer.Option("--milestone", help="Filter upstream issues by milestone title."),
    ] = None,
    label: Annotated[
        list[str] | None,
        typer.Option("--label", help="Filter upstream issues by label (repeatable)."),
    ] = None,
    default_test: Annotated[
        str | None,
        typer.Option(
            "--default-test",
            help="Fallback verification command when none is present in an issue.",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Target path for generated TASKS.md manifest."),
    ] = None,
    suggest_order: Annotated[
        bool,
        typer.Option(
            "--suggest-order/--no-suggest-order",
            help="Suggest topological & module-clustered execution order.",
        ),
    ] = True,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Display execution plan and manifest preview without writing to disk.",
        ),
    ] = False,
    fixture: Annotated[
        Path | None,
        typer.Option("--fixture", help="JSON/YAML fixture file for offline mock issue ingestion."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to .agymissioncontrol.yaml."),
    ] = None,
    project_dir: Annotated[
        Path | None,
        typer.Option("--project-dir", help="Project root directory."),
    ] = None,
) -> None:
    """Compile upstream issues into a dependency-ordered `TASKS.md` execution manifest."""
    cfg = load_config(config_path=config, project_dir=project_dir)
    assert cfg.paths is not None

    fallback_test = (default_test or cfg.execution.verify_command or "pytest").strip()
    target_path = output.resolve() if output is not None else cfg.paths.manifest_path

    try:
        raw_issues = _fetch_issues_for_plan(
            provider=provider,
            fixture=fixture,
            milestone=milestone,
            labels=label,
            issue=issue,
        )
    except TrackerError as exc:
        typer.echo(f"Error fetching issues: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not raw_issues:
        typer.echo("No matching issues found.")
        return

    compiled_tasks = [
        extract_task_spec(
            sanitize_issue_body(raw.body),
            raw.title,
            raw.number,
            fallback_test,
            labels=raw.labels,
            blocked_by_numbers=raw.blocked_by_numbers,
        )
        for raw in raw_issues
    ]

    existing_header: str | None = None
    try:
        if target_path.exists():
            existing_content = target_path.read_text(encoding="utf-8")
            parsed_header, existing_tasks = parse_manifest_document(existing_content)
            existing_header = parsed_header or None
            ordered_tasks = merge_manifests(
                existing_tasks,
                compiled_tasks,
                suggest_order=suggest_order,
            )
        else:
            ordered_tasks = DependencyResolver().resolve(
                compiled_tasks,
                suggest_order=suggest_order,
            )
    except (CyclicDependencyError, ValueError) as exc:
        typer.echo(f"Error resolving task dependencies: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    console = Console()
    _render_plan_tree(ordered_tasks, console)
    rendered_manifest = serialize_manifest(ordered_tasks, header=existing_header)

    if dry_run:
        typer.echo("\n--- Preview (--dry-run, not written to disk) ---")
        typer.echo(rendered_manifest, nl=False)
        return

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(rendered_manifest, encoding="utf-8")
    typer.echo(f"Wrote {len(ordered_tasks)} task(s) to {target_path}")


if __name__ == "__main__":
    app()
