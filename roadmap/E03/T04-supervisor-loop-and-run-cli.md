# Task E03-T04: Autonomous Supervisor Loop (`agymissioncontrol run`), Native `--detach` Backgrounding, Quota-Governed Execution, Token Metering & Reports

- **Task ID:** `E03-T04`
- **Parent Epic:** [Epic E03: Autonomous Execution Runner, Detached Supervisor & Telemetry (`run`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** 🧠 Deep Verify
- **Primary Agent:** `core-developer`
- **Dependencies:** `E03-T03`

---

## 1. User Story & Strategic Value

> **As an** Unattended Operations Lead running multiple agents across multiple projects,  
> **I want** an autonomous supervisor loop supporting a native `--detach` flag that executes tasks under quota governance, intercepts 429 rate limits to sleep automatically, and logs all state into isolated SQLite databases and reports without enforcing rigid Git branching policies,  
> **So that** I can fire-and-forget runs across repositories simultaneously, letting the calling project or agent manage its own Git branching and committing.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** Command `agymissioncontrol run` accepts options: `--project-dir`, `--manifest`, `--model`, `--fallback`, `--max-retries`, `--max-tasks`, `--max-hours`, `--max-token-budget`, `--stop-on-block`, `--fail-fast`, `--detach` (`-d`), `--credit-policy [never|budget_capped|always]`, `--max-credit-burn <USD>`, `--credit-trigger [weekly_only|any]`, and `--bypass-5h-quota`.
- [ ] **AC-2 (Caller Git Independence):**
  - The supervisor loop does NOT create Git branches, force PR creation, or wipe working trees.
  - Branching, staging, committing, and PR creation are strictly the responsibility of the calling agent or developer.
  - When a task completes or fails, the supervisor simply updates task status and telemetry in `missioncontrol.db` and `TASKS.md`.
- [ ] **AC-3 (Native Detached Backgrounding):**
  - When `--detach` is specified, uses `start_new_session=True` / double-fork to detach cleanly from the current terminal.
  - Redirects stdin to `/dev/null` and pipes stdout/stderr to `<project_root>/.agymissioncontrol/logs/supervisor.log`.
  - Parent prints formatted success message with spawned PID, log file path, and command to monitor (`agymissioncontrol ps` / `tail -f <log>`), then exits code `0`.
  - Child process continues running independently, updates its heartbeat in `~/.local/state/agymissioncontrol/registry.db`, and executes tasks sequentially.
- [ ] **AC-4 (Multi-Project Isolation):** All runtime artifacts (`<project_root>/TASKS.md`, `<project_root>/.agymissioncontrol/missioncontrol.db`, `<project_root>/.agymissioncontrol/logs/`, `<project_root>/.agymissioncontrol/reports/`) are strictly anchored to the resolved project directory.
- [ ] **AC-5 (Per-Task Quota & Overhead Metering):**
  - Captures exact tokens burned (work vs. mission control overhead).
  - Calculates `overhead_pct`, `cache_hit_rate`, and `estimated_cost_usd`.
  - Calculates approximate percentage of 5-hour rolling window and weekly quota consumed.
  - Tracks `is_credit_funded`, `credit_burn_usd`, and `credit_trigger_reason` for each task turn.
- [ ] **AC-6:** Live Terminal Summary (when running in foreground) prints execution duration, work tokens, overhead tokens, cache hit %, quota percentages used, and cumulative AI credit spend ($ burned / $ budget cap).
- [ ] **AC-7 (Circuit Breakers - Tokens & AI Credits):**
  - **Token Budget Ceiling:** If cumulative session tokens exceed `--max-token-budget` (or `max_session_tokens`), safely completes the active task and halts execution with `BUDGET_EXCEEDED` status.
  - **Credit Budget Ceiling:** If cumulative credit burn exceeds `--max-credit-burn` (or `credit.max_burn_usd`), halts execution with `CREDIT_BUDGET_EXCEEDED` status, preventing runaway billing.
  - **Quota Gating:** Prior to each task, evaluates dual-tier quota:
    - Depleted weekly quota triggers AI credit usage if policy permits (`WEEKLY_QUOTA_DEPLETED`).
    - Depleted 5-hour rolling quota triggers zero-token sleep (free) by default; only burns credits if caller explicitly passes `--credit-trigger any` or `--bypass-5h-quota`.
- [ ] **AC-8:** Dependency Skipping: If an upstream task is `BLOCKED`, downstream dependent tasks are automatically marked `[SKIPPED]` with zero tokens and zero quota spent.
- [ ] **AC-9:** Mid-turn rate limit recovery: Intercepts `429 / RESOURCE_EXHAUSTED`, parses reset epoch, sleeps, and retries the turn upon waking.
- [ ] **AC-10:** Morning Briefing Report: Queries `missioncontrol.db` to generate `<project_root>/.agymissioncontrol/reports/run_YYYYMMDD_HHMMSS.md` and `.json` containing task statistics, per-task token & quota consumption table, overhead ratio, retry tax, estimated costs, AI credit spend ($ burned, % of cap, trigger reasons), and zero-token sleep duration.
- [ ] **AC-11:** End-to-end integration test in `tests/test_e2e_run.py` simulating full lifecycle with multi-project isolation, detached backgrounding, credit governance, and overhead tracking.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/runner/supervisor.py` | Create/Modify | Orchestrator loop with detached background spawner, token ledger, overhead accounting, and dependency resolution |
| `src/agymissioncontrol/runner/report.py` | Create/Modify | Project-scoped morning briefing report generator querying `missioncontrol.db` |
| `src/agymissioncontrol/cli.py` | Modify | Register `--project-dir` and `--detach` across Typer CLI commands |
| `tests/test_e2e_run.py` | Create/Modify | Multi-project isolation, detached execution, and overhead simulation tests |

---

## 4. Technical Implementation Specification

### 4.1 Native Detached Process Spawner (`src/agymissioncontrol/runner/supervisor.py`)
```python
import os
import sys
import subprocess
from pathlib import Path
from rich.console import Console

console = Console()

def launch_detached_run(project_dir: Path, argv: list[str]) -> int:
    """Spawns agymissioncontrol run in a detached background process."""
    logs_dir = project_dir / ".agymissioncontrol" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    supervisor_log = logs_dir / "supervisor.log"

    # Strip --detach / -d from child arguments to avoid infinite spawn recursion
    child_args = [arg for arg in argv if arg not in ("--detach", "-d")]
    
    with open(supervisor_log, "a", encoding="utf-8") as log_out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "agymissioncontrol.cli"] + child_args[1:],
            stdout=log_out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # Detach from terminal session group
            cwd=str(project_dir),
        )
    
    console.print(f"[bold green]✓ Session detached in background![/bold green]")
    console.print(f"  • PID: [cyan]{proc.pid}[/cyan]")
    console.print(f"  • Project: [white]{project_dir.name}[/white]")
    console.print(f"  • Logs: [dim]{supervisor_log}[/dim]")
    console.print(f"\nMonitor live status via [bold]agymissioncontrol ps[/bold] or [bold]agymissioncontrol dashboard[/bold].")
    return proc.pid
```

---

## 5. Verification & Definition of Done

- [ ] Run `agymissioncontrol run --detach` in mock repo; verify parent exits 0 immediately, background child process is alive, and PID is registered in `registry.db`.
- [ ] Unit test verifies running on Project A and Project B sequentially writes reports strictly inside their respective `.agymissioncontrol/reports/` directories.
- [ ] Test verifying that `overhead_pct`, `cache_hit_rate`, and `estimated_cost_usd` are correctly computed from SQLite queries and rendered in both markdown and JSON report outputs.
- [ ] `uv run pytest tests/test_e2e_run.py` passes 100%.
