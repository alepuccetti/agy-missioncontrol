# Task E01-T03: Official Quota Snapshots Time-Series & SQLite Token Ledger Engine

- **Task ID:** `E01-T03`
- **Parent Epic:** [Epic E01: Quota Governor, Dual-Tier Quota & Session Circuit Breakers (`gate` / `status`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** 🧠 Deep Verify
- **Primary Agent:** `core-developer`
- **Dependencies:** `E01-T02`

---

## 1. User Story & Strategic Value

> **As an** Orchestration Engine and Analytics Dashboard,  
> **I want** an embedded SQLite ledger (`<project_dir>/.agymissioncontrol/missioncontrol.db`) that records time-series snapshots of official Google quota metrics (`agy -p "/usage" --output-format json`) alongside hard token expenditures (work vs. overhead tokens and costs),  
> **So that** go/no-go gating decisions are strictly driven by authoritative Google quota metrics, sleep durations are derived directly from Google-reported reset timestamps, time-series historical data is preserved for charting in the web cockpit, and token expenditures provide directional cost insights without complex speculative estimation.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1 (Zero-Dependency SQLite Ledger with Busy Timeout & WAL Mode):**
  - `src/agymissioncontrol/quota/ledger.py` uses Python standard library `sqlite3` to manage `<project_dir>/.agymissioncontrol/missioncontrol.db`.
  - Configures `sqlite3.connect(..., timeout=30.0)` with `PRAGMA journal_mode=WAL;`, `PRAGMA busy_timeout = 30000;`, `PRAGMA synchronous=NORMAL;`.
  - Sets `PRAGMA user_version = 1;` on schema initialization for forward migration safety.
- [ ] **AC-2 (Official Quota Snapshots Time-Series Schema):**
  - Initializes `quota_snapshots` table:
    ```sql
    CREATE TABLE IF NOT EXISTS quota_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        model_family TEXT NOT NULL,
        limit_type TEXT NOT NULL,
        remaining_pct REAL NOT NULL,
        reset_at TIMESTAMP NOT NULL,
        raw_line TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_quota_snapshots_ts 
    ON quota_snapshots(model_family, limit_type, timestamp);
    ```
- [ ] **AC-3 (Token Ledger Schema & Overhead Accounting):**
  - Initializes `token_transactions` table:
    ```sql
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
        total_tokens INTEGER NOT NULL,
        cost_usd REAL DEFAULT 0.0,
        allowance_pct_spent REAL DEFAULT 0.0,
        is_credit_funded INTEGER DEFAULT 0,
        credit_burn_usd REAL DEFAULT 0.0
    );
    CREATE INDEX IF NOT EXISTS idx_token_tx_model_ts 
    ON token_transactions(model, timestamp);
    ```
- [ ] **AC-4 (Time-Series Ingestion & Chart Queries):**
  - `record_quota_snapshots(records: List[QuotaSnapshotRecord], project_dir: Optional[Path] = None)`: Ingests parsed output from `agy -p "/usage" --output-format json` into `quota_snapshots` in global `~/.local/state/agymissioncontrol/registry.db` (and `<project_dir>/.agymissioncontrol/missioncontrol.db` when `project_dir` is provided).
  - `get_quota_history(project_dir: Optional[Path] = None, model_family: Optional[str] = None, limit: int = 100) -> List[dict]`: Queries historical time-series for dashboard charting (defaults to global `registry.db` so `agymissioncontrol dashboard` displays unified account-wide quota history regardless of current working directory).
- [ ] **AC-5 (Authoritative Sleep Duration Calculation):**
  - `calculate_sleep_duration(reset_at: datetime, safety_buffer_seconds: int = 60) -> int`:
    - Compares authoritative Google `reset_at` (UTC) with current UTC time (`datetime.now(timezone.utc)`).
    - If `now >= reset_at`, returns `0` seconds.
    - Otherwise, returns `int((reset_at - now).total_seconds()) + safety_buffer_seconds`.
    - Eliminates complex speculative math; relies strictly on the official reset epoch.
- [ ] **AC-6 (Directional Token Metrics & Credit Spend Queries):**
  - `get_session_token_totals(project_dir: Path, since: Optional[datetime] = None) -> UsageTelemetry`: Aggregates work vs. overhead tokens, cache hit rate, and total cost ($ USD).
  - `get_credit_spend(project_dir: Path, since: Optional[datetime] = None) -> float`: Aggregates cumulative credit spend in USD.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/quota/ledger.py` | Create | SQLite embedded database manager, `quota_snapshots` recorder, `token_transactions` recorder & time-series queries |
| `src/agymissioncontrol/quota/sliding_window.py` | Create | Authoritative sleep duration calculator and directional token allowance helper |
| `tests/test_sliding_window.py` | Create | Unit tests validating SQLite time-series insertion, history queries, and sleep calculation |

---

## 4. Technical Implementation Specification

### 4.1 SQLite Ledger Implementation (`src/agymissioncontrol/quota/ledger.py`)
```python
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from agymissioncontrol.quota.models import QuotaSnapshotRecord, UsageTelemetry

def get_global_registry_db_path() -> Path:
    state_dir = Path(os.environ.get("AGY_MC_STATE_DIR") or (Path.home() / ".local" / "state" / "agymissioncontrol"))
    return state_dir / "registry.db"

def _connect_sqlite(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn

def get_db(project_dir: Optional[Path] = None) -> sqlite3.Connection:
    if project_dir is None:
        return _connect_sqlite(get_global_registry_db_path())
    return _connect_sqlite(project_dir / ".agymissioncontrol" / "missioncontrol.db")

def _ensure_snapshot_table(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA user_version = 1;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quota_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            model_family TEXT NOT NULL,
            limit_type TEXT NOT NULL,
            remaining_pct REAL NOT NULL,
            reset_at TIMESTAMP NOT NULL,
            raw_line TEXT
        );
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_quota_snapshots_ts 
        ON quota_snapshots(model_family, limit_type, timestamp);
    """)

def init_ledger(project_dir: Path) -> None:
    with get_db(project_dir) as conn:
        _ensure_snapshot_table(conn)
        conn.execute("""
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
                total_tokens INTEGER NOT NULL,
                cost_usd REAL DEFAULT 0.0,
                allowance_pct_spent REAL DEFAULT 0.0,
                is_credit_funded INTEGER DEFAULT 0,
                credit_burn_usd REAL DEFAULT 0.0
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_token_tx_model_ts 
            ON token_transactions(model, timestamp);
        """)

def record_quota_snapshots(records: List[QuotaSnapshotRecord], project_dir: Optional[Path] = None) -> None:
    targets: List[Optional[Path]] = [None]
    if project_dir is not None:
        targets.append(project_dir)
    for target in targets:
        with get_db(target) as conn:
            _ensure_snapshot_table(conn)
            for r in records:
                conn.execute("""
                    INSERT INTO quota_snapshots (model_family, limit_type, remaining_pct, reset_at, raw_line, timestamp)
                    VALUES (?, ?, ?, ?, ?, datetime('now'));
                """, (r.model_family, r.limit_type, r.remaining_pct, r.reset_at.isoformat(), r.raw_line))

def get_quota_history(project_dir: Optional[Path] = None, model_family: Optional[str] = None, limit: int = 100) -> List[dict]:
    with get_db(project_dir) as conn:
        _ensure_snapshot_table(conn)
        if model_family:
            rows = conn.execute("""
                SELECT timestamp, model_family, limit_type, remaining_pct, reset_at 
                FROM quota_snapshots
                WHERE model_family = ?
                ORDER BY timestamp DESC, id DESC LIMIT ?;
            """, (model_family, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT timestamp, model_family, limit_type, remaining_pct, reset_at 
                FROM quota_snapshots
                ORDER BY timestamp DESC, id DESC LIMIT ?;
            """, (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]
```

### 4.2 Authoritative Sleep Duration (`src/agymissioncontrol/quota/sliding_window.py`)
```python
from datetime import datetime, timezone

def calculate_sleep_duration(reset_at: datetime, safety_buffer_seconds: int = 60) -> int:
    """Derives exact sleep seconds directly from Google-reported reset timestamp."""
    now_utc = datetime.now(timezone.utc)
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)
    
    delta = (reset_at - now_utc).total_seconds()
    if delta <= 0:
        return 0
    return int(delta) + safety_buffer_seconds
```

---

## 5. Verification & Definition of Done

- [ ] Unit tests verify `quota_snapshots` records are inserted with timestamps and queried by `get_quota_history`.
- [ ] Unit tests verify `calculate_sleep_duration` computes exact delta from `reset_at` with safety buffer.
- [ ] Unit tests verify `token_transactions` stores hard work vs overhead tokens and cost figures.
- [ ] Unit tests verify directional `allowance_pct_spent` is recorded for analytics without altering go/no-go gates.
- [ ] `uv run pytest tests/test_sliding_window.py` passes 100%.
