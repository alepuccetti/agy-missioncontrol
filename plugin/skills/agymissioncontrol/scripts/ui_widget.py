"""Antigravity IDE status pill and Markdown quota/gate renderer."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from agymissioncontrol.quota.locks import list_shared_model_locks
from agymissioncontrol.registry.manager import list_active_sessions
from agymissioncontrol.ui.server import build_quota_status_payload

DEFAULT_DASHBOARD_URL = "http://localhost:8484"


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def generate_ide_status_pill(dashboard_url: str = DEFAULT_DASHBOARD_URL) -> str:
    """Return clickable Markdown IDE status pill summarizing sleeping gates."""
    sessions = list_active_sessions()
    sleeping = [s for s in sessions if s.state == "SLEEPING" and s.wakes_at is not None]
    if not sleeping:
        return f"[💤 Mission Control: All Gates Clear]({dashboard_url})"

    earliest_wake = _ensure_utc(min(s.wakes_at for s in sleeping if s.wakes_at is not None))
    minutes_left = max(0, int((earliest_wake - datetime.now(UTC)).total_seconds() // 60))
    noun = "Gate" if len(sleeping) == 1 else "Gates"
    return f"[💤 {len(sleeping)} {noun} Sleeping ({minutes_left}m left)]({dashboard_url})"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def render_quota_status_markdown(dashboard_url: str = DEFAULT_DASHBOARD_URL) -> str:
    """Render live IDE status pill alongside pool quota, lock, and gate Markdown tables."""
    pill = generate_ide_status_pill(dashboard_url=dashboard_url)
    quota_payload = build_quota_status_payload()
    pools = quota_payload.get("pools", [])
    locks = list_shared_model_locks(include_expired=False)
    sessions = list_active_sessions()

    lines: list[str] = [
        pill,
        "",
        f"Cockpit: [{dashboard_url}]({dashboard_url})",
        "",
        "### Model Pool Quotas",
        "",
        "| Model Family | 5h Rolling | Weekly | Status |",
        "| :--- | :---: | :---: | :---: |",
    ]
    if pools:
        for pool in pools:
            status_label = "LOCKED" if pool.get("locked") else "HEALTHY"
            lines.append(
                f"| {pool['model_family']} | {_fmt_pct(pool.get('rolling_pct'))} | "
                f"{_fmt_pct(pool.get('weekly_pct'))} | {status_label} |"
            )
    else:
        lines.append("| (none recorded) | n/a | n/a | - |")

    lines.extend(["", "### Active Shared Locks", ""])
    if locks:
        lines.extend(
            [
                "| Pool | Owner PID | Reason | Remaining |",
                "| :--- | :---: | :--- | :---: |",
            ]
        )
        for lock in locks:
            owner = str(lock["locked_by_pid"]) if lock.get("locked_by_pid") is not None else "n/a"
            lines.append(
                f"| {lock['model']} | {owner} | {lock.get('reason') or '-'} | "
                f"{int(lock.get('remaining_seconds', 0))}s |"
            )
    else:
        lines.append("_No active shared pool locks._")

    lines.extend(["", "### Registered Gate Sessions", ""])
    if sessions:
        lines.extend(
            [
                "| PID | Project | Label | Model | Pool | State | Remaining Sleep | Burn |",
                "| :---: | :--- | :--- | :--- | :--- | :---: | :---: | :---: |",
            ]
        )
        for session in sessions:
            rem = (
                f"{session.remaining_sleep_seconds}s"
                if session.remaining_sleep_seconds is not None
                else "-"
            )
            lines.append(
                f"| {session.pid} | {session.project_name} | {session.label or '-'} | "
                f"{session.model} | {session.quota_group} | {session.state} | {rem} | "
                f"${session.credit_burn_usd:.2f} |"
            )
    else:
        lines.append("_No active or sleeping gate sessions registered._")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint printing either the IDE status pill or full Markdown status."""
    parser = argparse.ArgumentParser(description="Mission Control Antigravity IDE widget renderer.")
    parser.add_argument(
        "--dashboard-url",
        default=DEFAULT_DASHBOARD_URL,
        help="Local Quota Cockpit URL.",
    )
    parser.add_argument(
        "--pill-only",
        action="store_true",
        help="Print only the compact IDE status pill.",
    )
    args = parser.parse_args(argv)
    if args.pill_only:
        print(generate_ide_status_pill(dashboard_url=args.dashboard_url))
    else:
        print(render_quota_status_markdown(dashboard_url=args.dashboard_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
