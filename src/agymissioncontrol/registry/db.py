"""SQLite connection factory and schema initialization for global `registry.db`."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from agymissioncontrol.quota.ledger import _ensure_snapshot_table
from agymissioncontrol.quota.locks import configure_wal_connection, init_model_locks, table_exists


def get_state_dir() -> Path:
    """Return global state directory, honoring `AGY_MC_STATE_DIR` override."""
    override = os.environ.get("AGY_MC_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "state" / "agymissioncontrol"


def get_registry_db() -> sqlite3.Connection:
    """Open a WAL-configured SQLite connection to global `registry.db`."""
    db_path = get_state_dir() / "registry.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    configure_wal_connection(conn)
    return conn


def ensure_registry_schema(conn: sqlite3.Connection) -> None:
    """Ensure `sessions`, `model_locks`, and `quota_snapshots` exist on `conn`."""
    version_row = conn.execute("PRAGMA user_version;").fetchone()
    version = int(version_row[0]) if version_row is not None else 0
    if (
        version >= 1
        and table_exists(conn, "sessions")
        and table_exists(conn, "model_locks")
        and table_exists(conn, "quota_snapshots")
    ):
        return
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                pid INTEGER PRIMARY KEY,
                project_name TEXT NOT NULL,
                project_dir TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL,
                quota_group TEXT NOT NULL,
                state TEXT NOT NULL,
                sleep_started_at TIMESTAMP,
                wakes_at TIMESTAMP,
                credit_burn_usd REAL DEFAULT 0.0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        init_model_locks(conn)
        _ensure_snapshot_table(conn)
        if version < 1:
            conn.execute("PRAGMA user_version = 1;")


def init_registry() -> None:
    """Initialize `sessions`, `model_locks`, and `quota_snapshots` in `registry.db`."""
    conn = get_registry_db()
    try:
        ensure_registry_schema(conn)
    finally:
        conn.close()
