# Task E01-T05: CLI Subcommands (`agymissioncontrol status` & `agymissioncontrol gate`) with Dual-Tier Quota & Shared Model Lockout Cache

- **Task ID:** `E01-T05`
- **Parent Epic:** [Epic E01: Quota Governor, Dual-Tier Quota & Session Circuit Breakers (`gate` / `status`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `core-developer`
- **Dependencies:** `E01-T04`

---

## 1. User Story & Strategic Value

> **As an** Orchestration Engineer,  
> **I want** CLI subcommands `agymissioncontrol status` and `agymissioncontrol gate` displaying both continuous rolling capacity and weekly quota percentages while leveraging cross-project model locks,  
> **So that** I have full visibility into short-term burst limits and long-term weekly exhaustion, and sibling agent runs in other repositories immediately pause without burning redundant API calls when a model pool is locked.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** Command `agymissioncontrol status` queries `agy -p "/usage" --output-format json` (and logs a time-series snapshot) and outputs a Rich table detailing each model pool with authoritative dual-tier metrics and credit governance:
  - Rolling 5h Capacity (% remaining & official reset epoch)
  - Weekly Quota (% remaining and official reset epoch)
  - AI Credit Spend ($ spent / $ budget cap, active policy `never` | `budget_capped` | `always`, and trigger mode `weekly_only` | `any`)
  - Shared Lock Status (if model currently locked by another project)
  - Status badge (`HEALTHY`, `LOW_ROLLING`, `WEEKLY_DEPLETED`, `CREDIT_ACTIVE`, `LOCKED_OUT`)
- [ ] **AC-2:** Command `agymissioncontrol status --json` emits machine-readable JSON containing all dual-tier metrics and credit spend counters.
- [ ] **AC-3:** Command `agymissioncontrol gate` accepts:
  - `--model`: Model pool to check.
  - `--threshold`: Continuous 5-hour rolling threshold % (default: 15.0).
  - `--weekly-threshold`: Weekly quota threshold % (default: 10.0).
  - `--auto-sleep / --no-auto-sleep`: Block and sleep until quota recovers (default: auto-sleep).
  - `--max-sleep`: Maximum sleep duration in seconds.
  - `--credit-policy [never|budget_capped|always]`: Override project-level credit policy for this invocation.
  - `--max-credit-burn <USD>`: Override maximum dollar spend cap for this invocation.
  - `--credit-trigger [weekly_only|any]`: Override whether credits activate only when weekly quota is depleted (`weekly_only`, default) or also for 5-hour rolling limits (`any`).
  - `--bypass-5h-quota`: Convenience boolean flag to set `--credit-trigger any` and bypass 5-hour rolling sleep via credits.
- [ ] **AC-4 (Cross-Project Lock Interception):** Before making telemetry calls, `agymissioncontrol gate` checks `check_shared_model_lock(model)`. If another project locked the pool:
  - Immediately reuses the known reset epoch.
  - If `--auto-sleep` is enabled, sleeps until the lock epoch without making redundant CLI/API calls.
  - If auto-sleep is disabled, exits code `1` (`LOW_QUOTA`).
- [ ] **AC-5 (Gate Decision Matrix):**
  - **Weekly Quota < Weekly Threshold:**
    - If credit policy allows spending (`always` or `budget_capped` with `current_burn < max_burn`): logs credit warning, flags `CREDIT_ACTIVE`, and exits code `0` (`GO (CREDIT)`).
    - If credit policy is `never` or budget cap is reached: immediately exits code `2` (`HARD_LOCKOUT`, sleep cannot resolve a weekly lockout without credits).
  - **Rolling 5h Quota < Rolling Threshold (Weekly Quota Healthy):**
    - If 5h credit bypass is authorized (`--credit-trigger any` or `--bypass-5h-quota`) and credit policy allows spending: logs 5h credit bypass and exits code `0` (`GO (CREDIT)`).
    - Else (default behavior):
      - If `--auto-sleep` is disabled: exits code `1` (`LOW_QUOTA`).
      - If `--auto-sleep` is enabled: sets shared lock in `registry.db`, enters zero-token sleep until rolling reset; exits code `0` on wake.
  - **All Quotas >= Thresholds:** Exits code `0` (`GO (FREE)`).
- [ ] **AC-6:** Full test suite in `tests/test_cli_gate_status.py` verifying rolling, weekly, credit bypass, and shared lock gates.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/cli.py` | Create/Modify | `status` and `gate` commands with dual-tier flags, credit policy overrides, and shared lock check |
| `src/agymissioncontrol/ui/display.py` | Create/Modify | Rich table formatter displaying Rolling vs. Weekly quota and credit columns |
| `tests/test_cli_gate_status.py` | Create/Modify | CLI runner integration tests for dual-tier status, credit overrides, and gate exit codes |

---

## 4. Technical Implementation Specification

### 4.1 Rich Status Table Layout
```text
Model Pool        Rolling 5h (% Left)   Rolling Reset   Weekly Cap (% Left)   Weekly Reset      Credits ($ / Cap)     Trigger Mode   Shared Lock       Status
claude-sonnet     82%                   42m             64% (36% used)        Sun 00:00 UTC     $1.45 / $10.00        weekly_only    -                 HEALTHY
gemini-pro        100%                  -               88% (12% used)        Sun 00:00 UTC     $0.00 / $5.00         weekly_only    -                 HEALTHY
flash             12%                   24m             94% (6% used)         Sun 00:00 UTC     $0.00 / $5.00         weekly_only    Locked by proj-b  LOCKED_OUT
```

---

## 5. Verification & Definition of Done

- [ ] Run `uv run agymissioncontrol status` and verify dual-tier columns and lock statuses render cleanly.
- [ ] Test verifying that when a shared lock is active in `registry.db`, `agymissioncontrol gate` sleeps without making external telemetry calls.
- [ ] Test verifying that `agymissioncontrol gate --weekly-threshold 10` exits code `2` when mock weekly quota is at 5%.
- [ ] `uv run pytest tests/test_cli_gate_status.py` passes 100%.
