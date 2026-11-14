# Task E03-T03: Pre-Flight Baseline Test Sanity Check, Shell Verifier & Error-Injected Self-Correction Loop

- **Task ID:** `E03-T03`
- **Parent Epic:** [Epic E03: Autonomous Execution Runner, Detached Supervisor & Telemetry (`run`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `core-developer`
- **Dependencies:** `E03-T02`

---

## 1. User Story & Strategic Value

> **As an** Orchestrator and Quality Guardian,  
> **I want** a pre-flight baseline test check that ensures existing tests are healthy before the agent begins work, followed by deterministic shell verification and self-correction retries,  
> **So that** tokens are never burned attempting to fix pre-existing repository bugs, and transient implementation errors receive actionable failure traces.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** `src/agymissioncontrol/runner/verifier.py` implements `check_baseline(verification_command) -> BaselineStatus`.
- [ ] **AC-2:** If the verification command is expected to fail on a brand-new feature test, checks that baseline syntax/linter passes; if a full test suite check fails beforehand, halts execution with `BASELINE_DIRTY` warning.
- [ ] **AC-3:** Executes verification commands via `bash -c "<command>"` with duration and exit code capture.
- [ ] **AC-4:** Captures the trailing 30 lines of error output and formats an error diagnostic payload for retries.
- [ ] **AC-5:** Coordinates retry loop up to `max_retries_per_task` (default: 3), re-invoking the agent with error context on attempts $2..N$.
- [ ] **AC-6:** Unit tests in `tests/test_verifier.py` verify baseline checks, pass/fail classification, and error trace formatting.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/runner/verifier.py` | Create/Modify | Baseline validator, verification runner, output trimmer, and result models |
| `tests/test_verifier.py` | Create/Modify | Unit tests validating baseline checks and retry trace extraction |

---

## 4. Technical Implementation Specification

### 4.1 Verification Data Models
```python
class BaselineStatus(BaseModel):
    is_healthy: bool
    diagnostic_message: Optional[str] = None

class VerificationResult(BaseModel):
    passed: bool
    exit_code: int
    duration_seconds: float
    output_summary: str
    full_output: str
```

---

## 5. Verification & Definition of Done

- [ ] Unit test verifies dirty baseline triggers warning/abort.
- [ ] Unit test verifies clean command `exit 0` returns `passed=True`.
- [ ] Unit test verifies retry prompt injector includes trimmed error trace.
- [ ] `uv run pytest tests/test_verifier.py` passes 100%.
