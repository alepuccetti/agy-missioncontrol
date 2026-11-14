# Epic E01: Quota Governor, Gatekeeper & Session Circuit Breakers (`gate` / `status`)

- **Epic ID:** `E01`
- **Horizon:** Horizon 1 — Core Execution Engine
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Standard Teamwork
- **Primary Agents:** `core-developer`
- **Dependencies:** `E00` (Workspace Setup & Tooling)
- **Specification References:** [Project Specification §5 (AI Credit & Dual-Tier Quota Governance)](../../README.md#5-ai-credit--dual-tier-quota-governance), [§6 (CLI: gate, status)](../../README.md#6-cli-interface-specification)

---

## 1. Epic User Story & Strategic Value

> **As an** Autonomous Developer and Cost Controller,  
> **I want** a deterministic quota governor that retrieves authoritative live model quotas and reset timestamps directly via `agy -p "/usage" --output-format json`, logs time-series snapshots with timestamps in SQLite for trend charting, tracks tool overhead and token spend in an embedded ledger, executes zero-token blocking OS sleeps until official recovery epochs, and enforces operational circuit breakers,  
> **So that** unattended agent sessions make go/no-go gating decisions strictly against real Google quota data, track hard token metrics and directional allowance without complex speculative math, and never crash from rate limits.

---

## 2. Epic Objective & Architectural Scope

Establish the application configuration models (`.agymissioncontrol.yaml` with dual-tier quota thresholds and token budget caps), authoritative quota telemetry ingestion via `agy -p "/usage" --output-format json` (5-hour rolling + weekly ceiling with cross-project model lockout cache), historical time-series logging (`quota_snapshots` table) for dashboard charts, hard token expenditure ledger (`missioncontrol.db`) with overhead accounting and directional allowance metrics, zero-token sleep manager with Rich live countdowns, and CLI commands `agymissioncontrol status` and `agymissioncontrol gate`.

### Key Architectural Invariants Established in E01
1. **Authoritative Quota Telemetry via `agy -p "/usage" --output-format json`:**
   - Go/no-go gating decisions are strictly driven by the real values returned by `agy -p "/usage" --output-format json`.
   - Parses model families (e.g., `Gemini Models`, `Claude and GPT models`), limit types (`Five Hour Limit Remaining`, `Weekly Limit Remaining`), remaining percentages, and official reset timestamps (e.g., `"2026-11-11T10:12:48Z"`).
2. **Cross-Project Shared Model Lock Cache:**
   - Intercepts 429 rate-limits and records lock epochs in `~/.local/state/agymissioncontrol/registry.db`. Sibling processes check this cache before making telemetry calls to eliminate wasted polling.
3. **Time-Series Snapshots & Token Ledger (`missioncontrol.db`):**
   - Uses Python's standard library `sqlite3` in WAL mode with a 30s busy timeout inside `<project_dir>/.agymissioncontrol/missioncontrol.db` (zero external dependencies).
   - **`quota_snapshots` table:** Records every query from `agy -p "/usage" --output-format json` with UTC timestamp, model family, limit type, remaining %, and reset epoch for historical plotting and trend charts in the Web Cockpit.
   - **`token_transactions` table:** Records granular work vs overhead tokens, prompt cache hit rate, estimated dollar cost, and directional `allowance_pct_spent` (for informational cost tracking only, never for go/no-go decisions).
4. **Tool Overhead & Telemetry Accounting:**
   - Isolates Mission Control meta-tokens (prompt assembly, DoD parsing, verification) from subagent code generation tokens.
   - Computes live overhead %, prompt cache hit rate, and estimated session costs ($ USD).
5. **Zero-Token Quota Halting:** When authoritative rolling quota drops below `--threshold` (default 15%), execution enters a native OS sleep (`time.sleep`) calculated directly from the Google-reported reset timestamp + 60s safety buffer. Zero tokens are consumed while sleeping.
6. **AI Credit Governance & Weekly Quota Fallback:**
   - **Continuous 5-Hour Limit:** By default (`trigger: weekly_only`), performs zero-token sleep (free). Does NOT spend AI credits unless caller explicitly passes `--bypass-5h-quota` or `--credit-trigger any`.
   - **Weekly Quota Ceiling:** When weekly capacity drops below `--weekly-threshold` (default 10%), checks credit policy. If credits are active (`budget_capped` or `always`) and cumulative credit spend is below `max_burn_usd`, proceeds on AI credits (`is_credit_funded = True`). If credits are disabled (`never`) or budget is exhausted, immediately exits code `2` (`HARD_LOCKOUT`).
7. **Session Token & Credit Budget Caps:** Supports `max_session_tokens` to cap total cumulative token expenditure and `max_burn_usd` to cap total dollar spend across the run.

---

## 3. Epic-Level Acceptance Criteria

- [ ] **AC-1 (Configuration Validation):** Pydantic v2 schemas validate `.agymissioncontrol.yaml` including dual-tier thresholds (`threshold_percent`, `weekly_threshold_percent`), AI credit config (`policy`, `max_burn_usd`, `trigger`), and token budget limits (`max_session_tokens`).
- [ ] **AC-2 (Authoritative Quota Ingestion & Time-Series History):** Queries `agy -p "/usage" --output-format json` to retrieve exact 5-hour and weekly remaining percentages and reset epochs; logs historical snapshots into `quota_snapshots` table with timestamps for time-series chart plotting.
- [ ] **AC-3 (Dual-Tier Telemetry, Hard Token Ledger & Overhead Accounting):** Records granular work vs overhead tokens, cache hit rates, and estimated costs in `token_transactions`; computes directional allowance metrics without speculative math interfering with authoritative go/no-go gates.
- [ ] **AC-4 (Zero-Token Sleep Engine & Credit Bypass):** `time.sleep` blocks until the official reset epoch without token burn; silences ANSI spam in non-TTY mode with 60s heartbeat logs (QW-1); provides credit bypass for high-priority agents; cleanly traps `SIGINT` (Ctrl+C) with exit code `130`.
- [ ] **AC-5 (CLI Subcommands):** `agymissioncontrol status` prints real-time table with dual-tier and credit spend columns; `agymissioncontrol gate` enforces rolling, weekly, credit bypass, and shared lockout gates with exit codes `0`, `1`, `2`.

---

## 4. Tasks in This Epic

| Task ID | Title | Signal | Status |
| :--- | :--- | :---: | :---: |
| **[E01-T01](./T01-config-models-and-loader.md)** | Strongly Typed Configuration Models (`.agymissioncontrol.yaml` with Session Caps & Circuit Breakers) & Discovery Engine | ⚡ Direct | `[ ] TODO` |
| **[E01-T02](./T02-quota-telemetry-providers.md)** | Authoritative Quota Ingestion (`agy -p "/usage" --output-format json`), Cross-Project Cache & Overhead Accounting | 🧠 Deep Verify | `[ ] TODO` |
| **[E01-T03](./T03-sliding-window-math.md)** | Official Quota Snapshots Time-Series & SQLite Token Ledger Engine | 🧠 Deep Verify | `[ ] TODO` |
| **[E01-T04](./T04-zero-token-sleep-engine.md)** | Zero-Token Blocking Sleep Engine, Non-TTY Heartbeat Logging & Signal Interruption Handler | 🧠 Deep Verify | `[ ] TODO` |
| **[E01-T05](./T05-gate-and-status-cli.md)** | CLI Subcommands (`agymissioncontrol status` & `agymissioncontrol gate`) with Dual-Tier Quota & Shared Model Lockout Cache | ⚡ Direct | `[ ] TODO` |

---

## 5. Epic Completion Gate (Definition of Done)

- [ ] All configuration models and dual-tier boundary conditions verified with unit tests in `tests/test_config.py`.
- [ ] `AgyCliProvider` accurately deserializes `agy -p "/usage" --output-format json` into structured model quota objects and logs time-series snapshots to `quota_snapshots`.
- [ ] `agymissioncontrol status` prints formatted model pool table with both rolling 5h and weekly quota columns, shared lock status, and Mission Control overhead metrics.
- [ ] Sleep duration calculation verified to match official reset epoch + safety buffer.
- [ ] `task check` executes cleanly with 100% test pass rate.
