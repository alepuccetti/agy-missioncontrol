# Task E04-T01: SQLite-Backed Multi-Agent Process Registry (`registry.db`), Busy Timeout & Dead PID Pruning

- **Task ID:** `E04-T01`
- **Parent Epic:** [Epic E04: Multi-Agent Process Registry & Live Web Cockpit](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** 🧠 Deep Verify
- **Primary Agent:** `core-developer`
- **Dependencies:** `E03-T04`

---

## 1. User Story & Strategic Value

> **As an** Orchestration Architect,  
> **I want** an embedded SQLite process registry at `~/.local/state/agymissioncontrol/registry.db` operating in WAL mode with a 30-second busy timeout and dead PID cleanup,  
> **So that** concurrent `agymissioncontrol` runs across multiple projects update their state atomically without torn writes, file-locking deadlocks, or database busy collisions, while terminated agent processes are automatically pruned.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1 (Zero-Dependency Registry DB with Busy Timeout & Versioning - QW-4):**
  - Uses standard library `sqlite3` to maintain `~/.local/state/agymissioncontrol/registry.db`.
  - Configures `sqlite3.connect(..., timeout=30.0)`.
  - Executes `PRAGMA journal_mode=WAL;`, `PRAGMA busy_timeout = 30000;`, and `PRAGMA synchronous=NORMAL;` upon every connection.
  - Sets and verifies `PRAGMA user_version = 1;` on schema initialization for forward migration safety.
- [ ] **AC-2 (Schema Specification & Usage Serialization):**
  - `SessionState` includes a `@computed_field` `usage` dict (`work_tokens`, `overhead_tokens`, `overhead_pct`, `cache_hit_rate`, `estimated_cost_usd`) alongside the flat columns so both `agymissioncontrol ps` and `/api/sessions` (consumed by the TypeScript frontend) receive the exact expected shape.
  - Creates `sessions`, `model_locks`, and `quota_snapshots` tables in `registry.db`:
  ```sql
  CREATE TABLE IF NOT EXISTS sessions (
      pid INTEGER PRIMARY KEY,
      project_name TEXT NOT NULL,
      project_dir TEXT NOT NULL,
      task_id TEXT NOT NULL,
      task_title TEXT NOT NULL,
      model TEXT NOT NULL,
      state TEXT NOT NULL,
      sleep_started_at TIMESTAMP,
      wakes_at TIMESTAMP,
      work_tokens INTEGER DEFAULT 0,
      overhead_tokens INTEGER DEFAULT 0,
      estimated_cost_usd REAL DEFAULT 0.0,
      cache_hit_rate REAL DEFAULT 0.0,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );

  CREATE TABLE IF NOT EXISTS model_locks (
      model TEXT PRIMARY KEY,
      locked_until TIMESTAMP NOT NULL,
      reason TEXT NOT NULL,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );

  CREATE TABLE IF NOT EXISTS quota_snapshots (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      model_family TEXT NOT NULL,
      limit_type TEXT NOT NULL,
      remaining_pct REAL NOT NULL,
      reset_at TIMESTAMP NOT NULL,
      raw_line TEXT
  );
  ```
- [ ] **AC-3 (Lifecycle & Signal Control Operations):**
  - `get_state_dir() -> Path`: Resolves `~/.local/state/agymissioncontrol` (overridable via `AGY_MC_STATE_DIR` env var for test isolation).
  - `register_session(session: SessionState)`: Inserts or replaces session row on task start.
  - `update_session_state(pid: int, state: str, wakes_at: Optional[datetime])`: Atomically updates state and sleep wake epoch.
  - `unregister_session(pid: int)`: Deletes session row upon process completion via `atexit`.
  - `resolve_target_pids(...)`, `wake_target(...)`, `stop_target(...)`, `wake_session(pid)`, `stop_session(pid)`, `stop_all_sessions()` live in `src/agymissioncontrol/registry/manager.py` so CLI (`E04-T02`) and Web Server (`E04-T04`) can develop in parallel against one frozen contract.
- [ ] **AC-4 (Atomic Dead PID Pruning):**
  - `list_active_sessions() -> List[SessionState]`:
    - Queries all registered sessions.
    - Tests process liveness using `os.kill(pid, 0)`.
    - Automatically executes `DELETE FROM sessions WHERE pid IN (...)` for dead processes in an atomic transaction.
    - Returns only living, healthy sessions.
- [ ] **AC-5:** Unit tests in `tests/test_registry.py` verify concurrent registrations, status transitions, atomic pruning of terminated PIDs, and `atexit` cleanup.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/registry/models.py` | Create | Pydantic model for `SessionState` and status enum |
| `src/agymissioncontrol/registry/db.py` | Create | SQLite connection factory with WAL mode, 30s busy timeout, and schema initialization |
| `src/agymissioncontrol/registry/manager.py` | Create | Atomic session registration, heartbeat updates, and dead PID pruning engine |
| `tests/test_registry.py` | Create | Unit tests validating SQLite registry concurrency, lifecycle, and stale PID pruning |

---

## 4. Technical Implementation Specification

### 4.1 SQLite Registry Implementation (`src/agymissioncontrol/registry/db.py` & `manager.py`)
```python
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from agymissioncontrol.registry.models import SessionState

def get_state_dir() -> Path:
    return Path(os.environ.get("AGY_MC_STATE_DIR") or (Path.home() / ".local" / "state" / "agymissioncontrol"))

def get_registry_db() -> sqlite3.Connection:
    db_path = get_state_dir() / "registry.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn

def init_registry() -> None:
    with get_registry_db() as conn:
        conn.execute("PRAGMA user_version = 1;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                pid INTEGER PRIMARY KEY,
                project_name TEXT NOT NULL,
                project_dir TEXT NOT NULL,
                task_id TEXT NOT NULL,
                task_title TEXT NOT NULL,
                model TEXT NOT NULL,
                state TEXT NOT NULL,
                sleep_started_at TIMESTAMP,
                wakes_at TIMESTAMP,
                work_tokens INTEGER DEFAULT 0,
                overhead_tokens INTEGER DEFAULT 0,
                estimated_cost_usd REAL DEFAULT 0.0,
                cache_hit_rate REAL DEFAULT 0.0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_locks (
                model TEXT PRIMARY KEY,
                locked_until TIMESTAMP NOT NULL,
                reason TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
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

def is_pid_alive(pid: int) -> bool:
    """Tests process liveness using POSIX kill 0, correctly handling permission boundaries."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by a different user/context; it is still alive
        return True
    except OSError as e:
        import errno
        return e.errno != errno.ESRCH

def list_active_sessions() -> List[SessionState]:
    init_registry()
    with get_registry_db() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY state DESC, wakes_at ASC").fetchall()
        dead_pids = [r["pid"] for r in rows if not is_pid_alive(r["pid"])]
        if dead_pids:
            placeholders = ",".join("?" for _ in dead_pids)
            conn.execute(f"DELETE FROM sessions WHERE pid IN ({placeholders})", dead_pids)
            rows = [r for r in rows if r["pid"] not in dead_pids]
        
        return [SessionState(**dict(r)) for r in rows]
```

---

## 5. Verification & Definition of Done

- [ ] Unit tests in `tests/test_registry.py` verify that `registry.db` initializes with WAL mode and `busy_timeout = 30000`.
- [ ] Concurrent write test: Multiple threads registering sessions simulate parallel runners without locking errors.
- [ ] Dead PID test: Mocking `os.kill` to raise `ProcessLookupError` verifies immediate automatic deletion of stale rows.
- [ ] `uv run pytest tests/test_registry.py` passes 100%.
