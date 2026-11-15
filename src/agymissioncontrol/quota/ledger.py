"""SQLite embedded telemetry ledger for quota snapshots and token/credit accounting."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agymissioncontrol.quota.locks import get_registry_db_path, resolve_quota_group
from agymissioncontrol.quota.models import QuotaSnapshotRecord, UsageTelemetry
from agymissioncontrol.quota.provider import calculate_cost_usd, estimate_quota_impact


def get_global_registry_db_path() -> Path:
    """Return path to the global shared `registry.db` SQLite database."""
    return get_registry_db_path()


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA user_version = 1;")


@contextlib.contextmanager
def get_db(project_dir: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a WAL-configured SQLite connection and guarantee deterministic closure."""
    if project_dir is None:
        db_path = get_global_registry_db_path()
    else:
        db_path = project_dir.resolve() / ".agymissioncontrol" / "missioncontrol.db"

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        _configure_connection(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_snapshot_table(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA user_version = 1;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quota_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            model_family TEXT NOT NULL,
            limit_type TEXT NOT NULL,
            remaining_pct REAL NOT NULL,
            reset_at TIMESTAMP NOT NULL,
            raw_line TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_quota_snapshots_ts
        ON quota_snapshots (model_family, limit_type, timestamp);
        """
    )


def _ensure_token_transactions_table(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA user_version = 1;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            task_id TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            candidate_tokens INTEGER DEFAULT 0,
            thinking_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            work_tokens INTEGER DEFAULT 0,
            overhead_tokens INTEGER DEFAULT 0,
            retry_tax_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER NOT NULL,
            overhead_pct REAL DEFAULT 0.0,
            cost_usd REAL DEFAULT 0.0,
            allowance_pct_spent REAL DEFAULT 0.0,
            is_credit_funded INTEGER DEFAULT 0,
            credit_burn_usd REAL DEFAULT 0.0,
            credit_trigger_reason TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_token_tx_model_ts
        ON token_transactions (model, timestamp);
        """
    )


def init_ledger(project_dir: Path | None = None) -> None:
    """Initialize `quota_snapshots` and `token_transactions` schemas in SQLite."""
    with get_db(project_dir) as conn:
        _ensure_snapshot_table(conn)
        _ensure_token_transactions_table(conn)


def record_quota_snapshots(
    records: list[QuotaSnapshotRecord],
    project_dir: Path | None = None,
) -> None:
    """Persist time-series quota snapshots into global `registry.db` and project DB."""
    if not records:
        return

    rows = [
        (
            _ensure_utc(rec.recorded_at).isoformat(),
            rec.model_family,
            rec.limit_type,
            rec.remaining_pct,
            _ensure_utc(rec.reset_at).isoformat(),
            rec.raw_line,
        )
        for rec in records
    ]

    targets: list[Path | None] = [None]
    if project_dir is not None:
        targets.append(project_dir)

    for target in targets:
        with get_db(target) as conn:
            _ensure_snapshot_table(conn)
            conn.executemany(
                """
                INSERT INTO quota_snapshots (
                    timestamp, model_family, limit_type, remaining_pct, reset_at, raw_line
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


def get_quota_history(
    project_dir: Path | None = None,
    model_family: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query historical `quota_snapshots` ordered chronologically for trend charts."""
    with get_db(project_dir) as conn:
        _ensure_snapshot_table(conn)
        if model_family:
            canonical = resolve_quota_group(model_family)
            rows = conn.execute(
                """
                SELECT timestamp, model_family, limit_type, remaining_pct, reset_at, raw_line
                FROM quota_snapshots
                WHERE model_family IN (?, ?)
                ORDER BY datetime(timestamp) DESC, id DESC
                LIMIT ?
                """,
                (model_family, canonical, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT timestamp, model_family, limit_type, remaining_pct, reset_at, raw_line
                FROM quota_snapshots
                ORDER BY datetime(timestamp) DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]


def record_token_transaction(
    project_dir: Path,
    task_id: str,
    model: str,
    usage: UsageTelemetry,
    recorded_at: datetime | None = None,
) -> int:
    """Insert an execution token transaction and return its ledger row ID."""
    ts_iso = _ensure_utc(recorded_at or datetime.now(UTC)).isoformat()
    cost_usd = (
        usage.estimated_cost_usd
        if usage.estimated_cost_usd > 0.0
        else calculate_cost_usd(usage, model)
    )
    allowance_pct = (
        usage.allowance_pct_spent
        if usage.allowance_pct_spent > 0.0
        else estimate_quota_impact(
            usage.work_tokens, usage.overhead_tokens, model
        ).directional_allowance_pct
    )

    with get_db(project_dir) as conn:
        _ensure_token_transactions_table(conn)
        cursor = conn.execute(
            """
            INSERT INTO token_transactions (
                timestamp, task_id, model, prompt_tokens, candidate_tokens, thinking_tokens,
                cache_read_tokens, work_tokens, overhead_tokens, retry_tax_tokens,
                total_tokens, overhead_pct, cost_usd, allowance_pct_spent,
                is_credit_funded, credit_burn_usd, credit_trigger_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts_iso,
                task_id,
                model,
                usage.prompt_tokens,
                usage.candidate_tokens,
                usage.thinking_tokens,
                usage.cache_read_tokens,
                usage.work_tokens,
                usage.overhead_tokens,
                usage.retry_tax_tokens,
                usage.total_tokens,
                usage.overhead_pct,
                cost_usd,
                allowance_pct,
                1 if usage.is_credit_funded else 0,
                usage.credit_burn_usd,
                usage.credit_trigger_reason,
            ),
        )
        return int(cursor.lastrowid or 0)


def get_session_token_totals(
    project_dir: Path,
    since: datetime | None = None,
) -> UsageTelemetry:
    """Aggregate cumulative token, overhead, cost, and credit spend from the ledger."""
    with get_db(project_dir) as conn:
        _ensure_token_transactions_table(conn)
        params: list[Any] = []
        where_clause = ""
        if since is not None:
            where_clause = "WHERE datetime(timestamp) >= datetime(?)"
            params.append(_ensure_utc(since).isoformat())

        row = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                COALESCE(SUM(candidate_tokens), 0) AS candidate_tokens,
                COALESCE(SUM(thinking_tokens), 0) AS thinking_tokens,
                COALESCE(SUM(work_tokens), 0) AS work_tokens,
                COALESCE(SUM(overhead_tokens), 0) AS overhead_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(retry_tax_tokens), 0) AS retry_tax_tokens,
                COALESCE(SUM(cost_usd), 0.0) AS estimated_cost_usd,
                COALESCE(SUM(allowance_pct_spent), 0.0) AS allowance_pct_spent,
                COALESCE(MAX(is_credit_funded), 0) AS any_credit_funded,
                COALESCE(SUM(CASE WHEN is_credit_funded = 1 THEN credit_burn_usd ELSE 0.0 END), 0.0)
                    AS credit_burn_usd
            FROM token_transactions
            {where_clause}
            """,
            params,
        ).fetchone()

        if row is None:
            return UsageTelemetry()

        return UsageTelemetry(
            prompt_tokens=int(row["prompt_tokens"]),
            candidate_tokens=int(row["candidate_tokens"]),
            thinking_tokens=int(row["thinking_tokens"]),
            work_tokens=int(row["work_tokens"]),
            overhead_tokens=int(row["overhead_tokens"]),
            cache_read_tokens=int(row["cache_read_tokens"]),
            retry_tax_tokens=int(row["retry_tax_tokens"]),
            estimated_cost_usd=round(float(row["estimated_cost_usd"]), 6),
            is_credit_funded=bool(row["any_credit_funded"]),
            credit_burn_usd=round(float(row["credit_burn_usd"]), 6),
            allowance_pct_spent=round(float(row["allowance_pct_spent"]), 4),
        )


def get_credit_spend(
    project_dir: Path,
    since: datetime | None = None,
) -> float:
    """Return cumulative AI credit expenditure in USD for a project."""
    with get_db(project_dir) as conn:
        _ensure_token_transactions_table(conn)
        params: list[Any] = []
        where_clause = "WHERE is_credit_funded = 1"
        if since is not None:
            where_clause += " AND datetime(timestamp) >= datetime(?)"
            params.append(_ensure_utc(since).isoformat())

        query = (
            "SELECT COALESCE(SUM(credit_burn_usd), 0.0) AS total "
            f"FROM token_transactions {where_clause}"
        )
        row = conn.execute(query, params).fetchone()
        return round(float(row["total"]), 6) if row is not None else 0.0
