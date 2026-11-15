"""SQLite connection factory and schema initialization for global `registry.db`."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from agymissioncontrol.quota.ledger import _ensure_snapshot_table
from agymissioncontrol.quota.locks import init_model_locks


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
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_registry() -> None:
    """Initialize `sessions`, `model_locks`, and `quota_snapshots` in `registry.db`."""
    conn = get_registry_db()
    try:
        with conn:
            conn.execute("PRAGMA user_version = 1;")
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
    finally:
        conn.close()
