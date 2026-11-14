# Epic E03: Autonomous Execution Runner, Detached Supervisor & Telemetry (`run`)

- **Epic ID:** `E03`
- **Horizon:** Horizon 1 — Core Execution Engine (Release Milestone `v0.2.0-beta`)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Standard Teamwork
- **Primary Agents:** `core-developer`, `infra-specialist`
- **Dependencies:** `E00` (Setup), `E01` (Quota Governor)
- **Specification References:** [Project Specification §3 (Architecture & Workflow)](../../README.md#3-core-architecture--workflow), [§5 (AI Credit & Dual-Tier Quota Governance)](../../README.md#5-ai-credit--dual-tier-quota-governance), [§6 (CLI: run)](../../README.md#6-cli-interface-specification)

---

## 1. Epic User Story & Strategic Value

> **As an** Autonomous Operations Lead and Release Guardian,  
> **I want** a resilient execution supervisor supporting native `--detach` backgrounding that runs tasks under continuous quota governance, intercepts 429 rate limits, executes isolated agent turns with closed stdin, tracks tool overhead and token costs in SQLite, logs morning briefing summaries, broadcasts alerts, and compiles standalone releases—without imposing rigid Git branching or rollback policies,  
> **So that** I can run long-running background tasks across multiple codebases simultaneously with complete cost and quota control, letting the calling agent or developer manage Git branching and commits natively.

---

## 2. Epic Objective & Architectural Scope

Implement the context-isolated prompt assembler, headless `agy` CLI subprocess runner with effort governance, closed stdin, and token metering, pre-flight baseline test validator, deterministic shell verifier, autonomous supervisor loop with native `--detach` backgrounding, token accounting, dependency skipping, circuit breakers, morning briefing report generator, and public open-source CI/CD release workflow for standalone binaries.

### Key Architectural Invariants Established in E03
1. **Token Metering & Overhead Ledger:** Ingests `"usage"` telemetry from `agy --output-format json` CLI runs (`input_tokens`, `output_tokens`, `thinking_tokens`, `cache_read_tokens`, `total_tokens`). Tracks per-task spend, isolates Mission Control overhead, and enforces `--max-token-budget`.
2. **Pre-Flight Baseline Sanity Check:** Verifies clean test baseline before launching agent turns to prevent burning tokens on pre-existing errors.
3. **Absolute Context Isolation & Closed Stdin:** Spawns a brand-new `agy` process per task (`agy -p ... --dangerously-skip-permissions --mode accept-edits`) with `stdin=subprocess.DEVNULL` to guarantee no hanging on interactive queries.
4. **Caller Git Independence:** No automatic Git branching, PR creation, or working tree wiping. Git branching, staging, committing, and PR creation are strictly the responsibility of the calling agent or developer.
5. **Native Detached Backgrounding (`--detach` / `-d`):** Safely forks execution into the background, logging to `<project_root>/.agymissioncontrol/logs/supervisor.log` and registering in `registry.db`.
6. **Dependency-Aware Execution:** Automatically marks downstream tasks `[SKIPPED]` if an upstream dependency is `[BLOCKED]`.
7. **Morning Briefing Summary:** Generates `<project_root>/.agymissioncontrol/reports/run_YYYYMMDD_HHMMSS.md` with full statistics, token/quota breakdown, Mission Control overhead %, cache hit rate, and estimated costs.
8. **Public Open-Source Releases & Dual-Mode Execution:**
   - **Pinned Release Mode:** Pinned standalone binaries (including glibc-compatible `linux-x86_64` for Antigravity Remote Agent cloud VMs) and packaged plugin bundles on public GitHub Releases (`v1.0.0`).
   - **Bleeding-Edge Local Mode:** `task dev:link` (`uv tool install -e .` + plugin symlink) for uncommitted local development.

---

## 3. Epic-Level Acceptance Criteria

- [ ] **AC-1 (Context-Isolated Prompts):** Builds clean prompts with project rules (`AGENTS.md`), task spec, and DoD without history leakage.
- [ ] **AC-2 (Headless Execution & Closed Stdin):** Launches `agy -p` with effort governance, closed stdin, streaming output to `.agymissioncontrol/logs/<task_id>.log` and capturing prompt, completion, cache read, and thinking tokens.
- [ ] **AC-3 (Pre-Flight Check & Verification):** Validates clean baseline before start; verifies DoD via exit code `0`; injects failure traces on retries.
- [ ] **AC-4 (Supervisor Loop & Detached Backgrounding):** Supports `--detach` to run autonomously in the background without Git opinions, enforces dependency order, tracks cumulative token budget, handles mid-turn 429 recovery, and writes summary report with Mission Control overhead and cost analytics.
- [ ] **AC-5 (Public Release Pipeline & Remote VM Compatibility):** Automated GitHub Actions release workflow compiles multi-platform standalone binaries on pinned supported runners (`ubuntu-24.04`, `macos-15`, avoiding floating `-latest` aliases) and guarantees `linux-x86_64` runs cleanly on Antigravity Remote Agent cloud VMs without host dependencies, packages the Antigravity plugin, and supports downstream version pinning alongside local editable development.

---

## 4. Tasks in This Epic

| Task ID | Title | Signal | Status |
| :--- | :--- | :---: | :---: |
| **[E03-T01](./T01-prompt-assembler-and-effort.md)** | Context-Isolated Prompt Assembler & Reasoning Effort Profile Resolver | ⚡ Direct | `[ ] TODO` |
| **[E03-T02](./T02-headless-agy-launcher.md)** | Headless `agy` Subprocess Runner, Stdin Disconnection, Token & Quota Metering & Overhead Attribution | 🧠 Deep Verify | `[ ] TODO` |
| **[E03-T03](./T03-verifier-and-retry-loop.md)** | Pre-Flight Baseline Test Sanity Check, Shell Verifier & Error-Injected Self-Correction Loop | ⚡ Direct | `[ ] TODO` |
| **[E03-T04](./T04-supervisor-loop-and-run-cli.md)** | Autonomous Supervisor Loop (`agymissioncontrol run`), Native `--detach` Backgrounding, Quota-Governed Execution, Token Metering & Reports | 🧠 Deep Verify | `[ ] TODO` |
| **[E03-T05](./T05-release-pipeline-and-versioning.md)** | Public Open-Source Release Pipeline, Tagged Binaries & Version Pinning | ⚡ Direct | `[ ] TODO` |

---

## 5. Deferred Future Work: Operator Notifications

> **Architectural Note:** Desktop alerts (`osascript`, `notify-send`) and webhook dispatchers are deferred to post-v1 future enhancements (see [Deferred Operator Notifications](./deferred-operator-notifications.md)). The supervisor loop provides an optional callback interface `on_event(event: str, payload: dict)` for zero-overhead extensibility when notifications are wired.

---

## 6. Epic Completion Gate (Definition of Done)

- [ ] End-to-end simulation confirms headless execution with closed stdin, token ledger, and zero Git collisions.
- [ ] Detached background execution test verifies `agymissioncontrol run --detach` exits parent 0 and logs child output.
- [ ] Multi-project isolation tests verify zero cross-talk between different repository targets.
- [ ] Morning report verified to include Mission Control overhead %, cache hit %, and cost calculations.
- [ ] `task check` executes cleanly with 100% test pass rate.
