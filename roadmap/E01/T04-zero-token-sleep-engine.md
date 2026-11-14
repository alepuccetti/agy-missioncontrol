# Task E01-T04: Zero-Token Blocking Sleep Engine, Live Rich Countdown & Signal Interruption Handler

- **Task ID:** `E01-T04`
- **Parent Epic:** [Epic E01: Quota Governor, Dual-Tier Quota & Session Circuit Breakers (`gate` / `status`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** 🧠 Deep Verify
- **Primary Agent:** `core-developer`
- **Dependencies:** `E01-T03`

---

## 1. User Story & Strategic Value

> **As an** Unattended Runner Operator,  
> **I want** a zero-token blocking sleep mechanism with automatic TTY/headless detection and clean `SIGINT` interruption handling,  
> **So that** token burn is strictly halted at zero while waiting for quota replenishment, log files are kept clean of ANSI escape sequences in detached/cloud environments, and operators can cleanly abort without corrupted state.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** `src/agymissioncontrol/quota/governor.py` implements `evaluate_and_gate(...)` and `execute_sleep(seconds: int, model_name: str)`.
- [ ] **AC-2:** Consumes 0 API tokens during sleep using native OS sleep increments (`time.sleep(1)`).
- [ ] **AC-3:** Displays a Rich live progress bar rendering remaining hours, minutes, seconds, and recovery timestamp when attached to an interactive terminal (`sys.stdout.isatty() == True`).
- [ ] **AC-4:** Registers signal handlers for `SIGINT` (Ctrl+C) and `SIGTERM` to safely exit with code `130` and informative terminal message.
- [ ] **AC-5 (Automatic Non-TTY / Headless Detection - QW-1):** Automatically detects non-TTY environments (`not sys.stdout.isatty()`, such as `--detach` background runs, piped stdout, or Antigravity Remote Agent cloud VMs). In non-TTY mode, silences the Rich live progress bar to eliminate ANSI escape code spam (`\x1b[2K...`) and outputs clean, single-line periodic heartbeat logs once every 60 seconds.
- [ ] **AC-6 (AI Credit vs. Zero-Token Sleep Decision Matrix):**
  - Evaluates continuous 5-hour rolling capacity, weekly allowance, and `CreditConfig` (with caller CLI/env overrides).
  - **5-Hour Rolling Limit Depleted (Weekly Quota Healthy):**
    - **Default (`trigger: weekly_only`):** Executes zero-token sleep (free). Does NOT spend AI credits for 5-hour rolling burst limits.
    - **Override (`trigger: any` / `--bypass-5h-quota`):** If caller explicitly overrides trigger to `any` and credit policy is active (`ALWAYS` or `BUDGET_CAPPED` with budget remaining), bypasses sleep and proceeds with `is_credit_funded = True`.
  - **Weekly Allowance Depleted:**
    - If credit policy is active (`ALWAYS` or `BUDGET_CAPPED` with `current_credit_burn < max_burn_usd`), bypasses sleep and proceeds with `is_credit_funded = True` (reason: `WEEKLY_QUOTA_DEPLETED`).
    - If credit policy is `NEVER` or budget cap is reached, blocks execution with `HARD_LOCKOUT` (code 2) or sleeps until weekly reset if user specifically configured sleep for weekly resets.
  - **Both Healthy:** Proceeds with standard free allowance (`is_credit_funded = False`).

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/quota/governor.py` | Create | Quota governor engine, credit-vs-sleep decision matrix, Rich live countdown, headless logger, and signal handlers |
| `tests/test_governor.py` | Create | Unit and mock tests for sleep loop, credit bypass, interruption, and headless execution |

---

## 4. Technical Implementation Specification

### 4.1 Quota Governor Decision Matrix (`src/agymissioncontrol/quota/governor.py`)
```python
from enum import Enum
from typing import Optional
from agymissioncontrol.config import CreditConfig, CreditPolicy, CreditTrigger

class GateAction(str, Enum):
    PROCEED_FREE = "proceed_free"
    PROCEED_CREDIT = "proceed_credit"
    SLEEP_ROLLING = "sleep_rolling"
    LOCKOUT_WEEKLY = "lockout_weekly"
    LOCKOUT_CREDIT_BUDGET = "lockout_credit_budget"

def evaluate_gate_decision(
    rolling_quota_pct: float,
    rolling_threshold: float,
    weekly_quota_pct: float,
    weekly_threshold: float,
    credit_cfg: CreditConfig,
    current_credit_burn_usd: float,
    caller_bypass_5h: bool = False,
) -> tuple[GateAction, str]:
    # 1. Check weekly quota
    if weekly_quota_pct < weekly_threshold:
        if credit_cfg.policy == CreditPolicy.NEVER:
            return GateAction.LOCKOUT_WEEKLY, "Weekly quota depleted; AI credits disabled"
        if credit_cfg.policy == CreditPolicy.BUDGET_CAPPED and current_credit_burn_usd >= credit_cfg.max_burn_usd:
            return GateAction.LOCKOUT_CREDIT_BUDGET, f"Weekly quota depleted; credit budget (${credit_cfg.max_burn_usd:.2f}) exhausted"
        return GateAction.PROCEED_CREDIT, "Weekly quota depleted; continuing on AI credits"

    # 2. Check 5-hour rolling quota
    if rolling_quota_pct < rolling_threshold:
        allow_5h_credit = caller_bypass_5h or (credit_cfg.trigger == CreditTrigger.ANY)
        if allow_5h_credit and credit_cfg.policy != CreditPolicy.NEVER:
            if credit_cfg.policy == CreditPolicy.BUDGET_CAPPED and current_credit_burn_usd >= credit_cfg.max_burn_usd:
                return GateAction.SLEEP_ROLLING, f"5h rolling limit depleted; credit budget cap reached, falling back to sleep"
            return GateAction.PROCEED_CREDIT, "5h rolling limit depleted; bypassing via AI credits as requested"
        return GateAction.SLEEP_ROLLING, "5h rolling limit depleted; sleeping until free recovery (default zero-token policy)"

    return GateAction.PROCEED_FREE, "Quota healthy"
```

### 4.2 Sleep Loop Invariants & Headless Logging
- Sleep loop checks `time.monotonic()` against target deadline in 1-second increments (`time.sleep(1)`) while checking a `_wake_requested` flag (set by the `SIGUSR1` handler) so PEP 475 `time.sleep` auto-restart never prevents early wakeups.
- In interactive terminal mode (`sys.stdout.isatty() == True`), updates Rich `Progress` display once per second.
- In headless/non-interactive mode (`not sys.stdout.isatty()`):
  - Emits single-line log: `f"[SLEEP] Model {model} quota exhausted. Sleeping {seconds}s until {wakes_at.isoformat()}..."`
  - Logs heartbeat line every 60 seconds with remaining minutes.
- Traps `KeyboardInterrupt` / `signal.SIGINT`:
  ```python
  def _sigint_handler(signum, frame):
      console.print("\n[yellow]Sleep cancelled by user. Exiting safely...[/yellow]")
      sys.exit(130)
  ```

---

## 5. Verification & Definition of Done

- [ ] Mock test verifying that a 5-second sleep runs for ~5 seconds without consuming external calls.
- [ ] Test verifying that simulating `SIGINT` raises clean exit `130`.
- [ ] Execute `uv run pytest tests/test_governor.py` with 100% passing tests.
