# Task E04-T02: CLI Process Management: `ps`, `wake`, `stop` & `logs` for Detached Sessions

- **Task ID:** `E04-T02`
- **Parent Epic:** [Epic E04: Multi-Agent Process Registry & Live Web Cockpit](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** 🐝 Parallel
- **Primary Agent:** `core-developer`
- **Dependencies:** `E04-T01`

---

## 1. User Story & Strategic Value

> **As an** Operations Lead managing detached background runs across multiple repositories,  
> **I want** CLI commands `agymissioncontrol ps` to inspect active runs, `wake` to resume sleeping runners early, `stop` to gracefully abort runaway sessions, and `logs` to stream detached output,  
> **So that** I have complete, ergonomic terminal control over detached background agents without having to search for log paths or process IDs manually.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1 (`agymissioncontrol ps`):** Queries `registry.db` and outputs a Rich table of all active sessions across all projects with columns: `PID`, `Project`, `Task`, `Model`, `State`, `Remaining Sleep`, `Work Tokens`, `Overhead %`, `Est. Cost`. Supports `--json` flag.
- [ ] **AC-2 (`agymissioncontrol wake <TARGET>`):**
  - Resolves `<TARGET>` dynamically by **PID**, **project name**, or **task ID**.
  - Supports `--all` flag to wake all sleeping sessions across all projects.
  - Sends `signal.SIGUSR1` to target process(es) to trigger immediate quota re-evaluation.
- [ ] **AC-3 (`agymissioncontrol stop <TARGET>`):**
  - Resolves `<TARGET>` dynamically by **PID**, **project name**, or **task ID**.
  - Supports `--all` flag to stop all active sessions.
  - Sends `signal.SIGTERM` for graceful teardown (saving state and removing registry entry via `atexit`).
- [ ] **AC-4 (`agymissioncontrol logs [TARGET]`):**
  - Resolves target project directory.
  - Opens and follows `<project_dir>/.agymissioncontrol/logs/supervisor.log` (equivalent to `tail -f`).
  - Supports `--lines <N>` (default: 50).
- [ ] **AC-5 (Signal Handler in Governor):** Catching `SIGUSR1` during `time.sleep()` immediately terminates the sleep loop, performs a fresh quota check, and resumes task execution if quota is healthy.
- [ ] **AC-6:** Full CLI runner integration tests in `tests/test_cli_ps_wake.py` verifying PID, project, and task target resolution for both `wake` and `stop`.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/cli.py` | Modify | Register `ps`, `wake`, `stop`, and `logs` subcommands in Typer CLI |
| `src/agymissioncontrol/registry/manager.py` | Read / Verify | Consumes `resolve_target_pids`, `wake_target`, `stop_target` established in `E04-T01` |
| `src/agymissioncontrol/quota/governor.py` | Modify | Add `SIGUSR1` signal handler to break out of sleep loop early |
| `tests/test_cli_ps_wake.py` | Create | Unit tests validating `ps` table formatting, `wake` & `stop` signal dispatch, and log streaming |

---

## 4. Technical Implementation Specification

### 4.1 Target Resolver & Signal Operations (`src/agymissioncontrol/registry/manager.py`)
```python
import os
import signal
from typing import List, Optional
from agymissioncontrol.registry.db import get_registry_db

def resolve_target_pids(target: Optional[str] = None, match_all: bool = False, state_filter: Optional[str] = None) -> List[int]:
    """Resolves target identifiers (PID, project name, or task ID) to active PIDs."""
    with get_registry_db() as conn:
        query = "SELECT pid FROM sessions WHERE 1=1"
        params = []
        if state_filter:
            query += " AND state = ?"
            params.append(state_filter)

        if match_all:
            rows = conn.execute(query, params).fetchall()
            return [r["pid"] for r in rows]
        
        if not target:
            return []
            
        # 1. Direct PID integer
        if target.isdigit():
            pid = int(target)
            row = conn.execute(query + " AND pid = ?", params + [pid]).fetchone()
            return [row["pid"]] if row else []
        
        # 2. Project name match
        rows = conn.execute(query + " AND project_name LIKE ?", params + [f"%{target}%"]).fetchall()
        if rows:
            return [r["pid"] for r in rows]
            
        # 3. Task ID match
        rows = conn.execute(query + " AND task_id = ?", params + [target]).fetchall()
        return [r["pid"] for r in rows]

def wake_target(target: Optional[str] = None, wake_all: bool = False) -> List[int]:
    """Dispatches SIGUSR1 to wake sleeping processes."""
    pids = resolve_target_pids(target=target, match_all=wake_all, state_filter="SLEEPING")
    signaled = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGUSR1)
            signaled.append(pid)
        except OSError:
            pass
    return signaled

def stop_target(target: Optional[str] = None, stop_all: bool = False) -> List[int]:
    """Dispatches SIGTERM to gracefully stop active or sleeping processes."""
    pids = resolve_target_pids(target=target, match_all=stop_all)
    stopped = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except OSError:
            pass
    return stopped

def wake_session(pid: int) -> bool:
    """Convenience wrapper for dashboard API to wake a single PID."""
    return len(wake_target(target=str(pid))) > 0

def stop_session(pid: int) -> bool:
    """Convenience wrapper for dashboard API to stop a single PID."""
    return len(stop_target(target=str(pid))) > 0

def stop_all_sessions() -> List[int]:
    """Convenience wrapper for dashboard API to stop all sessions."""
    return stop_target(stop_all=True)
```

---

## 5. Verification & Definition of Done

- [ ] Run `uv run agymissioncontrol ps` and verify formatted table rendering with Overhead % and Cost columns.
- [ ] Test verifying that `agymissioncontrol wake <TARGET>` sends `SIGUSR1` to the matching PID.
- [ ] Test verifying that `agymissioncontrol stop <TARGET>` sends `SIGTERM` to the matching PID and unregisters it.
- [ ] Test verifying that `agymissioncontrol logs` resolves the active project and tails `supervisor.log`.
- [ ] `uv run pytest tests/test_cli_ps_wake.py` passes 100%.
