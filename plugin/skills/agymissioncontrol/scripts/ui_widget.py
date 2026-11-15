"""Antigravity IDE status pill and Markdown quota/gate renderer."""

from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from agymissioncontrol.quota.locks import list_shared_model_locks
    from agymissioncontrol.registry.manager import list_active_sessions
    from agymissioncontrol.ui.server import build_quota_status_payload
except ImportError:

    @dataclass
    class _FallbackSession:
        pid: int
        project_name: str
        label: str
        model: str
        quota_group: str
        state: str
        wakes_at: datetime | None
        credit_burn_usd: float

        @property
        def remaining_sleep_seconds(self) -> int | None:
            if self.state != "SLEEPING" or self.wakes_at is None:
                return None
            return max(0, int((_ensure_utc(self.wakes_at) - datetime.now(UTC)).total_seconds()))

    def _fallback_db_path() -> Path:
        override = os.environ.get("AGY_MC_STATE_DIR")
        base = (
            Path(override).expanduser()
            if override
            else Path.home() / ".local/state/agymissioncontrol"
        )
        return base / "registry.db"

    def _parse_iso(value: Any) -> datetime | None:
        if not value or not isinstance(value, str):
            return None
        try:
            return _ensure_utc(datetime.fromisoformat(value))
        except ValueError:
            return None

    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _open_readonly_registry() -> sqlite3.Connection | None:
        db_path = _fallback_db_path()
        if not db_path.exists():
            return None
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def list_shared_model_locks(include_expired: bool = False) -> list[dict[str, Any]]:
        conn = _open_readonly_registry()
        if conn is None:
            return []
        try:
            now_utc = datetime.now(UTC)
            rows = conn.execute(
                "SELECT model, locked_until, locked_by_pid, reason FROM model_locks"
            ).fetchall()
            locks: list[dict[str, Any]] = []
            for row in rows:
                locked_until = _parse_iso(row["locked_until"])
                if locked_until is None:
                    continue
                is_active = locked_until > now_utc
                if not include_expired and not is_active:
                    continue
                locks.append(
                    {
                        "model": str(row["model"]),
                        "locked_until": locked_until,
                        "remaining_seconds": max(0, int((locked_until - now_utc).total_seconds())),
                        "locked_by_pid": int(row["locked_by_pid"]),
                        "reason": str(row["reason"]),
                        "is_active": is_active,
                    }
                )
            return locks
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    def list_active_sessions(prune_dead: bool = False) -> list[_FallbackSession]:
        _ = prune_dead
        conn = _open_readonly_registry()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT pid, project_name, label, model, quota_group, state, "
                "wakes_at, credit_burn_usd FROM sessions"
            ).fetchall()
            sessions: list[_FallbackSession] = []
            for row in rows:
                pid = int(row["pid"])
                if not _pid_alive(pid):
                    continue
                sessions.append(
                    _FallbackSession(
                        pid=pid,
                        project_name=str(row["project_name"]),
                        label=str(row["label"] or ""),
                        model=str(row["model"]),
                        quota_group=str(row["quota_group"]),
                        state=str(row["state"]),
                        wakes_at=_parse_iso(row["wakes_at"]),
                        credit_burn_usd=float(row["credit_burn_usd"] or 0.0),
                    )
                )
            return sessions
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    def build_quota_status_payload() -> dict[str, Any]:
        conn = _open_readonly_registry()
        if conn is None:
            return {"pools": [], "locks": []}
        try:
            rows = conn.execute(
                "SELECT timestamp, model_family, limit_type, remaining_pct, reset_at "
                "FROM quota_snapshots ORDER BY datetime(timestamp) DESC, id DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()
        locks = list_shared_model_locks(include_expired=False)
        locked_models = {str(item["model"]) for item in locks}
        pools: dict[str, dict[str, Any]] = {}
        for row in rows:
            family = str(row["model_family"])
            entry = pools.setdefault(
                family,
                {
                    "model_family": family,
                    "rolling_pct": None,
                    "weekly_pct": None,
                    "locked": family in locked_models,
                },
            )
            tier = "weekly" if "week" in str(row["limit_type"]).lower() else "rolling"
            key = f"{tier}_pct"
            if entry[key] is None:
                entry[key] = float(row["remaining_pct"])
        return {"pools": list(pools.values()), "locks": locks}


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
