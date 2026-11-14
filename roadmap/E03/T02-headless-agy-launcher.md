# Task E03-T02: Headless `agy` Subprocess Runner, Stdin Disconnection, Token & Quota Metering & Overhead Attribution

- **Task ID:** `E03-T02`
- **Parent Epic:** [Epic E03: Autonomous Execution Runner, Detached Supervisor & Telemetry (`run`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** 🧠 Deep Verify
- **Primary Agent:** `core-developer`
- **Dependencies:** `E03-T01`

---

## 1. User Story & Strategic Value

> **As an** Unattended Runner and Cost Controller,  
> **I want** a subprocess launcher that executes headless `agy` CLI sessions with closed stdin and a strict timeout, captures turn-level token metrics (prompt, candidate, thinking, and cache read tokens), records transactions into the local SQLite ledger (`missioncontrol.db`), and tracks pre/post quota snapshots,  
> **So that** background processes never hang on unhandled interactive prompts, and each task runs in an isolated process with durable auditability of token burn, mission control overhead ratio, cache hit rate, and quota consumed.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** `src/agymissioncontrol/runner/agent.py` implements `AgyRunner.run_task(...) -> AgentRunResult`.
- [ ] **AC-2 (Prompt Hang Defense & Stdin Disconnection):**
  - Subprocess launches with `stdin=subprocess.DEVNULL` and `start_new_session=True` to guarantee unexpected permission queries fail fast and process trees can be terminated cleanly.
  - Passes native `--print-timeout <seconds>s` to `agy` and enforces a matching Python process-group watchdog (`os.killpg(proc.pid, signal.SIGKILL)` if exceeded), returning `TIMEOUT` (`124`) with captured log traces.
- [ ] **AC-3 (Headless Invocation Format):**
  ```bash
  agy -p "<prompt>" \
      --model <model> \
      --effort <effort> \
      --mode accept-edits \
      --print-timeout <seconds>s \
      --dangerously-skip-permissions \
      --output-format json
  ```
- [ ] **AC-4 (Telemetry Extraction):** Parses the top-level `"usage"` object from `agy --output-format json` turn responses (with fallback aliases for `UsageMetadata`) and builds `UsageTelemetry` (imported from `agymissioncontrol.quota.models`):
  - `input_tokens: int` (mapped to `prompt_tokens`)
  - `output_tokens: int` (mapped to `candidate_tokens`)
  - `thinking_tokens: int`
  - `cache_read_tokens: int`
  - `total_tokens: int`
- [ ] **AC-5 (Overhead Attribution):**
  - Attributes prompt assembly, planning, and verifier injection tokens as `overhead_tokens`.
  - Attributes model code edits and reasoning tokens as `work_tokens`.
  - Calculates `overhead_pct` and `cache_hit_rate`.
  - Calculates estimated cost via `calculate_cost_usd()`.
- [ ] **AC-6 (Durable SQLite Logging):** Appends turn transaction directly into `<project_dir>/.agymissioncontrol/missioncontrol.db` via `init_ledger()` / `record_transaction()`.
- [ ] **AC-7:** Ingests pre-task and post-task quota snapshots to measure empirical quota delta $\Delta Q\%$.
- [ ] **AC-8 (Clean Stdout/Stderr Separation & Log Writing):** Captures `stdout` (structured JSON) and `stderr` (diagnostic traces) on separate pipes so `stderr` output never corrupts JSON parsing, writes both streams to `.agymissioncontrol/logs/<task_id>_attempt_<n>.log`, and attaches telemetry to `AgentRunResult`.
- [ ] **AC-9:** Unit tests in `tests/test_agent_runner.py` mock CLI execution and verify command flags, closed stdin, cache token extraction, overhead calculation, SQLite persistence, and timeout handling.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/runner/models.py` | Create/Modify | `AgentRunResult` domain model (imports `UsageTelemetry` & `QuotaImpact` from `agymissioncontrol.quota.models`) |
| `src/agymissioncontrol/runner/agent.py` | Create/Modify | Subprocess supervisor with closed stdin, separate stdout/stderr pipes, JSON usage parser, overhead calculator & SQLite recorder |
| `tests/test_agent_runner.py` | Create/Modify | Unit tests for subprocess spawning, timeout defense, and token telemetry parsing |

---

## 4. Technical Implementation Specification

### 4.1 Subprocess Execution Guard (`src/agymissioncontrol/runner/agent.py`)
```python
import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Optional, Tuple

def execute_headless_agy(
    prompt: str,
    model: str,
    effort: str,
    log_path: Path,
    timeout_seconds: int = 900,
    cwd: Optional[Path] = None,
) -> Tuple[int, Optional[dict], str]:
    """Executes agy CLI in headless mode with closed stdin, isolated stdout/stderr, and strict timeout."""
    cmd = [
        "agy", "-p", prompt,
        "--model", model,
        "--effort", effort,
        "--mode", "accept-edits",
        "--print-timeout", f"{timeout_seconds}s",
        "--dangerously-skip-permissions",
        "--output-format", "json",
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,  # Prevent hanging on interactive prompts
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,    # Own process group for clean subtree termination
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    try:
        stdout_data, stderr_data = proc.communicate(timeout=timeout_seconds)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            proc.kill()
        stdout_data, stderr_data = proc.communicate()
        stderr_data = (stderr_data or "") + f"\n[ERROR] Task execution exceeded timeout ceiling of {timeout_seconds}s.\n"
        exit_code = 124  # Standard timeout exit code

    with open(log_path, "w", encoding="utf-8") as log_file:
        if stdout_data:
            log_file.write(stdout_data)
            if not stdout_data.endswith("\n"):
                log_file.write("\n")
        if stderr_data:
            log_file.write("\n--- STDERR ---\n")
            log_file.write(stderr_data)

    parsed_json = None
    if stdout_data:
        for line in reversed(stdout_data.strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    parsed_json = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if parsed_json is None:
            try:
                parsed_json = json.loads(stdout_data)
            except json.JSONDecodeError:
                pass

    return exit_code, parsed_json, stderr_data or ""
```

---

## 5. Verification & Definition of Done

- [ ] Mock test verifying that `stdin=subprocess.DEVNULL` is set on the subprocess and `stdout`/`stderr` are captured without cross-corruption.
- [ ] Timeout test verifying that processes hanging past timeout duration are terminated cleanly with exit code `124`.
- [ ] Mock test verifying that `"usage"` (`input_tokens`, `output_tokens`, `thinking_tokens`, `cache_read_tokens`, `total_tokens`) and pre/post quota snapshots are attached to `AgentRunResult`.
- [ ] Test verifying that `overhead_tokens` and `overhead_pct` are calculated accurately.
- [ ] Test verifying that transaction record is inserted into `<project_dir>/.agymissioncontrol/missioncontrol.db`.
- [ ] `uv run pytest tests/test_agent_runner.py` passes 100%.
