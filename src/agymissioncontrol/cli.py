"""Typer CLI entrypoint for agyMissionControl."""

from __future__ import annotations

import atexit
import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

import agymissioncontrol
from agymissioncontrol.config import (
    CreditPolicy,
    CreditTrigger,
    atomic_write_text,
    ensure_gitignore_guard,
    load_config,
)
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
    build_quota_report,
    clear_all_shared_model_locks,
    clear_shared_model_lock,
    evaluate_and_gate,
    get_credit_spend,
    get_session_token_totals,
    list_shared_model_locks,
    parse_since_arg,
    render_report_json,
    render_report_markdown,
    render_report_rich,
    resolve_quota_group,
    write_quota_report,
)
from agymissioncontrol.registry import (
    SessionState,
    SessionStatus,
    list_active_sessions,
    register_session,
    stop_target,
    unregister_session,
    update_session_state,
    wake_target,
)
from agymissioncontrol.ui import (
    run_dashboard_server,
    spawn_detached_dashboard,
    stop_detached_dashboard,
)
from agymissioncontrol.ui.display import build_status_payload, render_status_table

app = typer.Typer(
    name="agymissioncontrol",
    help="Quota governor, zero-token sleep controller, and live quota cockpit for agy.",
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
    label: Annotated[
        str,
        typer.Option(
            "--label",
            help="Optional Orchestrator task or workstream label.",
        ),
    ] = "",
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

    pid = os.getpid()
    active_model = cfg.models.default_model
    quota_group = resolve_quota_group(active_model)
    gate_started = datetime.now(UTC)

    def _safe_unregister() -> None:
        try:
            unregister_session(pid)
        except Exception:  # noqa: BLE001
            pass

    def _on_gate_event(event: str, payload: dict[str, Any]) -> None:
        if event == "SLEEP_ENTERED":
            wakes_raw = payload.get("wakes_at")
            wakes_dt = datetime.fromisoformat(str(wakes_raw)) if wakes_raw else None
            update_session_state(
                pid,
                SessionStatus.SLEEPING,
                wakes_at=wakes_dt,
                sleep_started_at=datetime.now(UTC),
                credit_burn_usd=get_credit_spend(root),
            )
        elif event == "SLEEP_WOKEN":
            update_session_state(
                pid,
                SessionStatus.ACTIVE,
                wakes_at=None,
                sleep_started_at=None,
                credit_burn_usd=get_credit_spend(root),
            )

    register_session(
        SessionState(
            pid=pid,
            project_name=root.name,
            project_dir=str(root),
            label=label,
            model=active_model,
            quota_group=quota_group,
            state=SessionStatus.ACTIVE,
            credit_burn_usd=get_credit_spend(root),
        )
    )
    atexit.register(_safe_unregister)

    try:
        while True:
            effective_max_sleep = cfg.governor.max_sleep_seconds
            if effective_max_sleep is not None:
                elapsed = int((datetime.now(UTC) - gate_started).total_seconds())
                effective_max_sleep = max(0, effective_max_sleep - elapsed)

            decision = evaluate_and_gate(
                model=active_model,
                provider=provider,
                credit_cfg=cfg.governor.credits,
                rolling_threshold=cfg.governor.threshold_percent,
                weekly_threshold=cfg.governor.weekly_threshold_percent,
                safety_buffer_seconds=cfg.governor.safety_buffer_seconds,
                auto_sleep=cfg.execution.auto_sleep,
                max_sleep_seconds=effective_max_sleep,
                caller_bypass_5h=bypass_5h_quota,
                project_dir=root,
                on_event=_on_gate_event,
                max_session_tokens=cfg.governor.max_session_tokens,
            )
            if decision.woken_early:
                update_session_state(
                    pid,
                    SessionStatus.ACTIVE,
                    wakes_at=None,
                    sleep_started_at=None,
                    credit_burn_usd=get_credit_spend(root),
                )
                continue
            break
    except (RuntimeError, ValueError) as exc:
        typer.echo(f"Error evaluating quota gate: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        _safe_unregister()

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

    atomic_write_text(target_path, rendered_manifest, encoding="utf-8")
    typer.echo(f"Wrote {len(ordered_tasks)} task(s) to {target_path}")


class ReportFormat(StrEnum):
    """Supported output formats for `agymissioncontrol report`."""

    RICH = "rich"
    MD = "md"
    JSON = "json"


@app.command()
def report(
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            help="Filter ledger history (e.g. '30m', '24h', '7d', or ISO-8601 timestamp).",
        ),
    ] = None,
    fmt: Annotated[
        ReportFormat,
        typer.Option(
            "--format",
            "-f",
            help="Report output format: rich | md | json.",
        ),
    ] = ReportFormat.RICH,
    write: Annotated[
        bool,
        typer.Option(
            "--write/--no-write",
            help="Write report artifact under .agymissioncontrol/reports/.",
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
) -> None:
    """Generate quota pool, token overhead, and AI credit spend reports from SQLite."""
    try:
        since_dt = parse_since_arg(since)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="'--since'") from exc

    cfg = load_config(config_path=config, project_dir=project_dir)
    assert cfg.paths is not None
    quota_report = build_quota_report(cfg, since=since_dt)

    if fmt == ReportFormat.JSON:
        typer.echo(render_report_json(quota_report))
    elif fmt == ReportFormat.MD:
        typer.echo(render_report_markdown(quota_report), nl=False)
    else:
        render_report_rich(quota_report)

    if write:
        ensure_gitignore_guard(cfg.paths.project_root)
        written_path = write_quota_report(
            quota_report,
            cfg.paths.reports_dir,
            fmt=fmt.value,
        )
        typer.echo(f"Wrote quota report to {written_path}", err=(fmt == ReportFormat.JSON))


@app.command()
def locks(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit shared model locks as JSON."),
    ] = False,
    include_expired: Annotated[
        bool,
        typer.Option("--include-expired", help="Include expired lock entries in output."),
    ] = False,
) -> None:
    """List active cross-project shared model locks from global `registry.db`."""
    entries = list_shared_model_locks(include_expired=include_expired)
    if json_output:
        payload = [
            {
                **entry,
                "locked_until": entry["locked_until"].isoformat(),
                "updated_at": entry["updated_at"].isoformat(),
            }
            for entry in entries
        ]
        typer.echo(json.dumps(payload, indent=2))
        return

    if not entries:
        typer.echo("No active shared model locks.")
        return

    table = Table(
        title="agyMissionControl — Shared Model Locks",
        show_header=True,
        header_style="bold yellow",
    )
    table.add_column("Model Pool", style="bold", no_wrap=True)
    table.add_column("Locked Until")
    table.add_column("Remaining", justify="right")
    table.add_column("PID", justify="right")
    table.add_column("Reason")
    table.add_column("Active")

    for entry in entries:
        table.add_row(
            str(entry["model"]),
            entry["locked_until"].isoformat(),
            f"{entry['remaining_seconds']}s",
            str(entry["locked_by_pid"]),
            str(entry["reason"]),
            "yes" if entry["is_active"] else "expired",
        )
    Console().print(table)


@app.command()
def unlock(
    model: Annotated[
        str | None,
        typer.Argument(help="Model ID or canonical quota pool name to unlock."),
    ] = None,
    all_locks: Annotated[
        bool,
        typer.Option("--all", help="Clear all shared model locks in `registry.db`."),
    ] = False,
) -> None:
    """Clear shared model locks in global `registry.db`."""
    if not all_locks and not model:
        typer.echo("Error: specify a MODEL argument or pass --all.", err=True)
        raise typer.Exit(code=1)

    if all_locks:
        cleared = clear_all_shared_model_locks()
        typer.echo(f"Cleared {cleared} shared model lock(s).")
        return

    assert model is not None
    group = resolve_quota_group(model)
    clear_shared_model_lock(model)
    typer.echo(f"Cleared shared lock for {group} ({model}).")


@app.command()
def ps(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit registered gate sessions as JSON."),
    ] = False,
) -> None:
    """List active and sleeping `agymissioncontrol gate` sessions in `registry.db`."""
    sessions = list_active_sessions()
    if json_output:
        typer.echo(json.dumps([session.model_dump(mode="json") for session in sessions], indent=2))
        return

    if not sessions:
        typer.echo("No active or sleeping gate sessions.")
        return

    table = Table(
        title="agyMissionControl — Active & Sleeping Gates",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("PID", justify="right", style="bold", no_wrap=True)
    table.add_column("Project")
    table.add_column("Label")
    table.add_column("Model")
    table.add_column("Pool")
    table.add_column("State", no_wrap=True)
    table.add_column("Remaining Sleep", justify="right", no_wrap=True)
    table.add_column("Credit Burn", justify="right", no_wrap=True)

    for session in sessions:
        remaining = (
            f"{session.remaining_sleep_seconds}s"
            if session.remaining_sleep_seconds is not None
            else "-"
        )
        table.add_row(
            str(session.pid),
            session.project_name,
            session.label or "-",
            session.model,
            session.quota_group,
            str(session.state),
            remaining,
            f"${session.credit_burn_usd:.2f}",
        )
    console = Console()
    if console.width < 140:
        console = Console(width=140)
    console.print(table)


@app.command()
def wake(
    target: Annotated[
        str | None,
        typer.Argument(help="Target PID, project name, label, or model/pool name."),
    ] = None,
    all_sessions: Annotated[
        bool,
        typer.Option("--all", help="Wake all sleeping gate sessions via SIGUSR1."),
    ] = False,
) -> None:
    """Send `SIGUSR1` to wake matching sleeping gate session(s) for immediate re-check."""
    if not all_sessions and not target:
        typer.echo("Error: specify a TARGET argument or pass --all.", err=True)
        raise typer.Exit(code=1)

    woken_pids = wake_target(target=target, match_all=all_sessions)
    if not woken_pids:
        typer.echo("No matching sleeping gate sessions found.")
        raise typer.Exit(code=1)

    pid_list = ", ".join(str(pid) for pid in woken_pids)
    typer.echo(f"Woke sleeping gate session(s): {pid_list}")


@app.command()
def stop(
    target: Annotated[
        str | None,
        typer.Argument(help="Target PID, project name, label, or model/pool name."),
    ] = None,
    all_sessions: Annotated[
        bool,
        typer.Option("--all", help="Stop all active/sleeping gate sessions via SIGTERM."),
    ] = False,
) -> None:
    """Send `SIGTERM` for graceful teardown of matching gate session(s)."""
    if not all_sessions and not target:
        typer.echo("Error: specify a TARGET argument or pass --all.", err=True)
        raise typer.Exit(code=1)

    stopped_pids = stop_target(target=target, match_all=all_sessions)
    if not stopped_pids:
        typer.echo("No matching gate sessions found.")
        raise typer.Exit(code=1)

    pid_list = ", ".join(str(pid) for pid in stopped_pids)
    typer.echo(f"Sent SIGTERM to gate session(s): {pid_list}")


@app.command()
def dashboard(
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Localhost port for cockpit HTTP server."),
    ] = 8484,
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Do not open the web browser automatically."),
    ] = False,
    detach: Annotated[
        bool,
        typer.Option("--detach", "-d", help="Run the dashboard server in the background."),
    ] = False,
    stop_server: Annotated[
        bool,
        typer.Option("--stop", help="Stop a running detached dashboard server via SIGTERM."),
    ] = False,
) -> None:
    """Serve the live Quota Cockpit web dashboard locally on `127.0.0.1`."""
    if stop_server:
        stopped, _pid, message = stop_detached_dashboard()
        typer.echo(message)
        if not stopped and "invalid" in message.lower():
            raise typer.Exit(code=1)
        return

    open_browser = not no_browser
    if detach:
        try:
            pid = spawn_detached_dashboard(port=port, open_browser=open_browser)
        except (RuntimeError, OSError) as exc:
            typer.echo(f"Error starting detached dashboard server on port {port}: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"Quota Cockpit running at http://localhost:{port} (PID {pid})")
        return

    typer.echo(f"Serving Quota Cockpit at http://localhost:{port} (Press Ctrl+C to stop)")
    try:
        run_dashboard_server(port=port, open_browser=open_browser)
    except OSError as exc:
        typer.echo(f"Error starting dashboard server on port {port}: {exc}", err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
