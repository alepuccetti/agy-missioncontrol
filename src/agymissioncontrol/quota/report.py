"""Quota telemetry reporting, `--since` window filtering, and Markdown/JSON export."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agymissioncontrol.config import AppConfig, resolve_project_paths
from agymissioncontrol.quota.ledger import (
    get_credit_spend,
    get_db,
    get_session_token_totals,
    init_ledger,
)
from agymissioncontrol.quota.locks import list_shared_model_locks
from agymissioncontrol.quota.models import UsageTelemetry

_DURATION_PATTERN = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)
_DURATION_UNITS: dict[str, str] = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


class PoolReportSummary(BaseModel):
    """Aggregated snapshot statistics for a single model pool and limit tier."""

    model_family: str
    limit_type: str
    latest_remaining_pct: float
    min_remaining_pct: float
    max_remaining_pct: float
    latest_reset_at: str
    snapshot_count: int


class QuotaReport(BaseModel):
    """Project-scoped quota history, token/overhead metrics, credits, and active locks."""

    project_name: str
    project_dir: str
    generated_at: datetime
    since: datetime | None = None
    pools: list[PoolReportSummary] = Field(default_factory=list)
    usage: UsageTelemetry = Field(default_factory=UsageTelemetry)
    credit_policy: str
    credit_burn_usd: float = 0.0
    credit_cap_usd: float = 0.0
    credit_trigger_counts: dict[str, int] = Field(default_factory=dict)
    active_locks: list[dict[str, Any]] = Field(default_factory=list)


def parse_since_arg(raw: str | None, now: datetime | None = None) -> datetime | None:
    """Parse a relative duration (`30m`, `24h`, `7d`, `2w`) or ISO-8601 timestamp into UTC."""
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None

    reference = (
        now.astimezone(UTC) if now is not None and now.tzinfo else (now or datetime.now(UTC))
    )
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)

    match = _DURATION_PATTERN.fullmatch(stripped)
    if match is not None:
        amount = int(match.group(1))
        if amount <= 0:
            raise ValueError(f"Duration must be positive, got: {raw!r}")
        unit_key = _DURATION_UNITS[match.group(2).lower()]
        return reference - timedelta(**{unit_key: amount})

    try:
        parsed = datetime.fromisoformat(stripped)
    except ValueError as exc:
        raise ValueError(
            f"Invalid --since value {raw!r}. Use durations like '30m', '24h', '7d' or ISO-8601."
        ) from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _serialize_lock(lock: dict[str, Any]) -> dict[str, Any]:
    serialized = dict(lock)
    for key in ("locked_until", "updated_at"):
        value = serialized.get(key)
        if isinstance(value, datetime):
            serialized[key] = value.isoformat()
    return serialized


def build_quota_report(
    config: AppConfig,
    since: datetime | None = None,
    generated_at: datetime | None = None,
) -> QuotaReport:
    """Build a project-scoped `QuotaReport` from `missioncontrol.db` and `registry.db`."""
    paths = config.paths or resolve_project_paths()
    project_dir = paths.project_root
    init_ledger(project_dir)

    pools: list[PoolReportSummary] = []
    trigger_counts: dict[str, int] = {}

    with get_db(project_dir) as conn:
        snap_params: list[Any] = []
        snap_where = ""
        if since is not None:
            since_utc = since.replace(tzinfo=UTC) if since.tzinfo is None else since.astimezone(UTC)
            snap_where = "WHERE datetime(timestamp) >= datetime(?)"
            snap_params.append(since_utc.isoformat())

        snap_rows = conn.execute(
            f"""
            SELECT timestamp, model_family, limit_type, remaining_pct, reset_at
            FROM quota_snapshots
            {snap_where}
            ORDER BY datetime(timestamp) ASC, id ASC
            """,
            snap_params,
        ).fetchall()

        grouped: dict[tuple[str, str], list[Any]] = {}
        for row in snap_rows:
            key = (str(row["model_family"]), str(row["limit_type"]))
            grouped.setdefault(key, []).append(row)

        for (family, limit_type), rows in sorted(grouped.items()):
            pcts = [float(r["remaining_pct"]) for r in rows]
            latest = rows[-1]
            pools.append(
                PoolReportSummary(
                    model_family=family,
                    limit_type=limit_type,
                    latest_remaining_pct=round(float(latest["remaining_pct"]), 2),
                    min_remaining_pct=round(min(pcts), 2),
                    max_remaining_pct=round(max(pcts), 2),
                    latest_reset_at=str(latest["reset_at"]),
                    snapshot_count=len(rows),
                )
            )

        tx_params: list[Any] = []
        tx_where = "WHERE is_credit_funded = 1"
        if since is not None:
            since_utc = since.replace(tzinfo=UTC) if since.tzinfo is None else since.astimezone(UTC)
            tx_where += " AND datetime(timestamp) >= datetime(?)"
            tx_params.append(since_utc.isoformat())

        tx_rows = conn.execute(
            f"""
            SELECT COALESCE(NULLIF(credit_trigger_reason, ''), 'unspecified') AS reason,
                   COUNT(*) AS cnt
            FROM token_transactions
            {tx_where}
            GROUP BY reason
            ORDER BY reason ASC
            """,
            tx_params,
        ).fetchall()
        trigger_counts = {str(r["reason"]): int(r["cnt"]) for r in tx_rows}

    usage = get_session_token_totals(project_dir, since=since)
    credit_burn = get_credit_spend(project_dir, since=since)
    locks = [_serialize_lock(lock) for lock in list_shared_model_locks(include_expired=False)]
    now_ts = generated_at or datetime.now(UTC)
    if now_ts.tzinfo is None:
        now_ts = now_ts.replace(tzinfo=UTC)

    return QuotaReport(
        project_name=project_dir.name or config.project.name,
        project_dir=str(project_dir),
        generated_at=now_ts,
        since=since,
        pools=pools,
        usage=usage,
        credit_policy=config.governor.credits.policy.value,
        credit_burn_usd=credit_burn,
        credit_cap_usd=config.governor.credits.max_burn_usd,
        credit_trigger_counts=trigger_counts,
        active_locks=locks,
    )


def render_report_rich(report: QuotaReport, console: Console | None = None) -> None:
    """Render a structured Rich quota and credit spend report to terminal output."""
    target_console = console or Console()
    since_label = report.since.isoformat() if report.since is not None else "all-time"
    header = (
        f"[bold]Project:[/bold] {report.project_name} ({report.project_dir})\n"
        f"[bold]Generated:[/bold] {report.generated_at.isoformat()}   "
        f"[bold]Window:[/bold] {since_label}"
    )
    target_console.print(Panel(header, title="agyMissionControl Quota Report", border_style="cyan"))

    pool_table = Table(title="Quota Pool Telemetry", show_header=True, header_style="bold cyan")
    pool_table.add_column("Model Family", style="bold")
    pool_table.add_column("Tier")
    pool_table.add_column("Latest %", justify="right")
    pool_table.add_column("Min %", justify="right")
    pool_table.add_column("Snapshots", justify="right")
    pool_table.add_column("Latest Reset")

    if report.pools:
        for pool in report.pools:
            pool_table.add_row(
                pool.model_family,
                pool.limit_type,
                f"{pool.latest_remaining_pct:.1f}%",
                f"{pool.min_remaining_pct:.1f}%",
                str(pool.snapshot_count),
                pool.latest_reset_at,
            )
    else:
        pool_table.add_row("(no snapshots recorded)", "-", "-", "-", "0", "-")
    target_console.print(pool_table)

    usage = report.usage
    usage_summary = (
        f"Total Tokens: {usage.total_tokens:,} "
        f"(Work: {usage.work_tokens:,}, Overhead: {usage.overhead_tokens:,}, "
        f"Overhead %: {usage.overhead_pct:.1f}%)\n"
        f"Cache Read: {usage.cache_read_tokens:,} "
        f"(Cache Hit %: {usage.cache_hit_rate:.1f}%)   "
        f"Est. Cost: ${usage.estimated_cost_usd:.4f}\n"
        f"AI Credits ({report.credit_policy}): "
        f"${report.credit_burn_usd:.4f} / ${report.credit_cap_usd:.2f}"
    )
    target_console.print(Panel(usage_summary, title="Token & Credit Spend Summary"))

    if report.active_locks:
        lock_table = Table(
            title="Active Shared Locks", show_header=True, header_style="bold yellow"
        )
        lock_table.add_column("Model Pool", style="bold")
        lock_table.add_column("PID", justify="right")
        lock_table.add_column("Remaining", justify="right")
        lock_table.add_column("Reason")
        lock_table.add_column("Locked Until")
        for lock in report.active_locks:
            lock_table.add_row(
                str(lock["model"]),
                str(lock["locked_by_pid"]),
                f"{lock['remaining_seconds']}s",
                str(lock["reason"]),
                str(lock["locked_until"]),
            )
        target_console.print(lock_table)


def render_report_markdown(report: QuotaReport) -> str:
    """Render `QuotaReport` as clean, portable Markdown."""
    since_label = report.since.isoformat() if report.since is not None else "All time"
    lines = [
        f"# Quota & Credit Report: {report.project_name}",
        "",
        f"- **Project Directory:** `{report.project_dir}`",
        f"- **Generated At:** `{report.generated_at.isoformat()}`",
        f"- **Since:** `{since_label}`",
        "",
        "## Quota Pool Summary",
        "",
        "| Model Family | Limit Tier | Latest % | Min % | Max % | Snapshots | Latest Reset |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | :--- |",
    ]

    if report.pools:
        for pool in report.pools:
            lines.append(
                f"| {pool.model_family} | {pool.limit_type} | "
                f"{pool.latest_remaining_pct:.1f}% | {pool.min_remaining_pct:.1f}% | "
                f"{pool.max_remaining_pct:.1f}% | {pool.snapshot_count} | "
                f"`{pool.latest_reset_at}` |"
            )
    else:
        lines.append("| _(none)_ | - | - | - | - | 0 | - |")

    usage = report.usage
    lines.extend(
        [
            "",
            "## Token & AI Credit Summary",
            "",
            f"- **Total Tokens:** {usage.total_tokens:,}",
            f"- **Work Tokens:** {usage.work_tokens:,}",
            f"- **Overhead Tokens:** {usage.overhead_tokens:,} ({usage.overhead_pct:.1f}%)",
            f"- **Cache Read Tokens:** {usage.cache_read_tokens:,} "
            f"(hit rate {usage.cache_hit_rate:.1f}%)",
            f"- **Estimated Cost (USD):** ${usage.estimated_cost_usd:.4f}",
            f"- **Credit Policy:** `{report.credit_policy}`",
            f"- **AI Credit Burn / Cap (USD):** ${report.credit_burn_usd:.4f} / "
            f"${report.credit_cap_usd:.2f}",
        ]
    )

    if report.credit_trigger_counts:
        lines.extend(["", "### Credit Trigger Breakdown", ""])
        for reason, count in report.credit_trigger_counts.items():
            lines.append(f"- `{reason}`: {count} transaction(s)")

    lines.extend(["", "## Active Shared Model Locks", ""])
    if report.active_locks:
        lines.extend(
            [
                "| Model Pool | PID | Remaining (s) | Reason | Locked Until |",
                "| :--- | ---: | ---: | :--- | :--- |",
            ]
        )
        for lock in report.active_locks:
            lines.append(
                f"| {lock['model']} | {lock['locked_by_pid']} | "
                f"{lock['remaining_seconds']} | `{lock['reason']}` | "
                f"`{lock['locked_until']}` |"
            )
    else:
        lines.append("No active shared model locks.")

    return "\n".join(lines) + "\n"


def render_report_json(report: QuotaReport) -> str:
    """Render `QuotaReport` as formatted JSON."""
    return report.model_dump_json(indent=2)


def write_quota_report(
    report: QuotaReport,
    reports_dir: Path,
    fmt: Literal["rich", "md", "json"] = "md",
) -> Path:
    """Persist Markdown or JSON report under `.agymissioncontrol/reports/`."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.astimezone(UTC).strftime("%Y%m%d_%H%M%S")
    if fmt == "json":
        path = reports_dir / f"quota_report_{stamp}.json"
        path.write_text(render_report_json(report) + "\n", encoding="utf-8")
    else:
        path = reports_dir / f"quota_report_{stamp}.md"
        path.write_text(render_report_markdown(report), encoding="utf-8")
    return path
