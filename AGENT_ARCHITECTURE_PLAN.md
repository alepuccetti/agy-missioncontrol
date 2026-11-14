# agyMissionControl: Orchestrator-First Architecture & Governance Plan

> **Document Status:** Approved Pre-Implementation Plan (v2.0 — Orchestrator-First)  
> **Project:** `agymissioncontrol`  
> **Execution Model:** Each Epic (`E00`–`E04`) starts with a single **Orchestrator** session that executes sequentially by default, applies extra verification on high-risk tasks, and parallelizes only where files are decoupled (`E04`).

---

## 1. Design Philosophy

`agymissioncontrol` is a focused Python CLI package (`src/agymissioncontrol/`) plus a lightweight TypeScript/Alpine web cockpit (`src/agymissioncontrol/ui/frontend/`, added in `E04`). Because each epic starts on the **Orchestrator** model, we avoid heavy multi-agent ceremony (no pre-built `.agents/subagents/` or `.agents/skills/` boilerplate) and focus strictly on:

1. **Non-Negotiable Project Invariants:** Machine-checked rules that every task must respect.
2. **Orchestrator-First Execution:** The Orchestrator writes code and tests directly by default—zero delegation overhead on straightforward Python modules.
3. **Risk-Based Verification & Selective Parallelism:** Extra adversarial/concurrency testing on high-risk tasks (`🧠 Deep Verify`), and parallel workers only when modules are genuinely independent (`🐝 Parallel` in `E04`).

---

## 2. Repository Layout

```text
agy-missioncontrol/
├── AGENTS.md                              # Concise root rules (<50 lines)
├── AGENT_ARCHITECTURE_PLAN.md             # This Orchestrator-first governance plan
├── .agents/
│   ├── hooks.json                         # PreToolUse hook wiring command_guard.py (E00-T03)
│   └── scripts/command_guard.py           # Deterministic PreToolUse shell guardrail (E00-T03)
├── plugin/                                # Distributable Antigravity plugin (E00-T03, E03-T05)
├── src/agymissioncontrol/
│   ├── cli.py                             # Typer CLI entrypoint
│   ├── config.py                          # Pydantic v2 config & project discovery (E01-T01)
│   ├── quota/                             # Authoritative agy /usage provider, cache & sleep (E01)
│   ├── manifest/                          # Issue adapters, sanitizer, DoD & TASKS.md AST (E02)
│   ├── runner/                            # Prompt assembler, headless agy, verifier & supervisor (E03)
│   ├── registry/                          # Global SQLite registry.db & PID management (E04-T01)
│   └── ui/                                # Zero-dependency HTTP server, frontend/ & static/ (E04)
├── tests/                                 # Pytest suite (unit, signal, concurrency & integration)
├── scripts/                               # setup_labels.sh, package_plugin.py
├── roadmap/                               # Master roadmap & atomic specs (ROADMAP.md, E00..E04)
├── Taskfile.yml                           # Unified task runner (dev:link, check, build:binary, ui:build)
└── pyproject.toml                         # hatchling, dependencies, ruff & pytest config
```

---

## 3. Core Project Invariants & Mechanical Gates

1. **macOS & Linux Only:** All subprocess spawning, POSIX signals (`SIGINT`, `SIGTERM`, `SIGUSR1`), PID checks (`os.kill(pid, 0)`), PyInstaller `--add-data` (`:`), and CI runners (`ubuntu-24.04`, `macos-15`) target macOS and Linux exclusively. No Windows fallbacks.
2. **Authoritative Quota Gating:** Go/no-go gating and sleep reset timestamps derive **strictly** from `agy -p "/usage" --output-format json` (and shared lockout cache). Token ledgers (`token_transactions`) and directional `allowance_pct_spent` are for analytics/charts only—never for gating. Map `gemini-pro-orchestrator-g1-pro` (`Orchestrator (Argon)`) to `"Gemini 4 Argon"`.
3. **Subprocess & SQLite Safety:**
   - Headless `agy -p` calls must set `stdin=subprocess.DEVNULL`, separate `stdout`/`stderr` pipes, `start_new_session=True`, and process-group kill (`os.killpg`) on timeout alongside native `--print-timeout`.
   - Local `missioncontrol.db` and global `~/.local/state/agymissioncontrol/registry.db` (overridable via `AGY_MC_STATE_DIR` for tests) must enable `PRAGMA journal_mode=WAL;` and `busy_timeout = 30000`. Always normalize SQLite timestamp comparisons with `datetime(...)`.
4. **KISS & Dependency Discipline:**
   - Keep modules flat and modular (~300 LOC target for functional modules; up to ~500 LOC for schemas, AST parsers, and test suites).
   - Do not add unapproved dependencies. In particular, `E04-T04` must use Python stdlib `http.server` (no FastAPI/Flask) so standalone PyInstaller binaries stay lean.
   - Use permissive open-source licenses only (`MIT`, `Apache-2.0`, `BSD-*`, `ISC`, `PSF`).
5. **External Virtualenv & Mandatory Verification Gate (`task check`):**
   - Virtual environments must live **outside the repository** at `~/.virtualenvs/agymissioncontrol` (via `UV_PROJECT_ENVIRONMENT`), keeping the repo free of `.venv/` directories.
   - Every task must pass `ruff format --check`, `ruff check`, and `pytest` on Python `3.15` (`.python-version` = `3.15`) before marking `[x] DONE` in `roadmap/`.
6. **Mandatory Task & Epic GitHub Issue Body Synchronization:**
   - Updating only the local `roadmap/` markdown or the parent Epic issue is insufficient. For **every task (`E0X-T0Y`) AND parent epic (`E0X`)**, once `task check` passes and the signed commit is created:
     1. Update local `roadmap/E0X/T0Y-*.md`, `roadmap/E0X/EPIC.md`, and `roadmap/ROADMAP.md` (`- **Status:** \`[x] DONE\``, `- **Implementing Commits (\`main\`):**`, and `[x]` on all AC, Task table, and Verification / DoD checkboxes).
     2. Propagate those completed statuses, checked Acceptance Criteria / Verification / DoD checkboxes, and implementing commit links to the **GitHub Task Issue** (`gh issue edit <task-issue> --body-file ...`) and **GitHub Epic Issue** (`gh issue edit <epic-issue> --body-file ...`) **before** calling `gh issue close`, preserving each issue's top callout header (`> **Parent Epic:**`, `> **Blocked by:**`, `### Tracked Tasks`) and rewriting relative `../../README.md` links to GitHub `blob/main/README.md`.

---

## 4. Orchestrator Execution Signals (How Each Epic Runs)

Instead of rigid subagent ceremonies, each task carries a lightweight execution signal for the Orchestrator:

| Signal | Meaning for the Orchestrator |
| :--- | :--- |
| **`⚡ Direct`** | **Implement + unit test directly** in one pass, run `task check`, update local `roadmap/` + GitHub issue body (`gh issue edit`), then close issue. No subagent delegation needed. |
| **`🧠 Deep Verify`** | **High-risk core logic** (security guard, SQLite WAL/concurrency, `agy /usage` gating, AST/DAG parsing, subprocess isolation, supervisor loop). Write thorough edge-case/signal/concurrency tests and run a final diff/spec audit (self-review or read-only `code-reviewer`) before marking `[x] DONE` locally and syncing the GitHub issue body (`gh issue edit`). |
| **`🐝 Parallel`** | **Decoupled modules (`E04-T02`–`T04`)** that can run as parallel workers once `E04-T01` locks `registry.db`, target/signal helpers, and `SessionState` contracts. Keep workers scoped to non-overlapping paths (`cli.py` vs `ui/frontend/` vs `ui/server.py`) and shut them down immediately on completion. |

---

## 5. Per-Task Signal Matrix (25 Tasks)

| Epic | Task ID | Title | Signal | Focus |
| :--- | :--- | :--- | :---: | :--- |
| **E00** | `E00-T01` | Git Init, MIT License & `.gitignore` | `⚡ Direct` | Completed (`[x] DONE`). |
| | `E00-T02` | Python Packaging (`pyproject.toml`) & `uv` | `⚡ Direct` | Python 3.15 (`.python-version`) + `agymissioncontrol --version`. |
| | `E00-T03` | Antigravity Hooks, `plugin.json` & `command_guard.py` | `🧠 Deep Verify` | Test destructive command blocks & `PreToolUse` JSON schema. |
| | `E00-T04` | `Taskfile.yml` & Pinned LTS CI Workflow | `⚡ Direct` | `dev:link`, `build:binary`, `ci`, `ubuntu-24.04` / `macos-15`. |
| | `E00-T05` | GitHub CLI Remote Link & Issue Labels | `⚡ Direct` | Pause for `gh auth login` if unauthenticated. |
| **E01** | `E01-T01` | Config Models (`.agymissioncontrol.yaml`) & Loader | `⚡ Direct` | Pydantic v2 validation & config discovery. |
| | `E01-T02` | Authoritative Quota Ingestion (`agy /usage`), Cache & Overhead | `🧠 Deep Verify` | Test `agy /usage` JSON parsing & `resolve_quota_group()`. |
| | `E01-T03` | Quota Snapshots Time-Series & SQLite Token Ledger | `🧠 Deep Verify` | Verify WAL mode, `30000ms` timeout & atomic writes. |
| | `E01-T04` | Zero-Token Sleep Engine, Heartbeat & Signals | `🧠 Deep Verify` | Test `SIGINT` (exit `130`), `SIGUSR1` wakeup & non-TTY logs. |
| | `E01-T05` | `status` & `gate` CLI Subcommands | `⚡ Direct` | Wire `E01-T02`..`T04` into Typer/Rich + CLI tests. |
| **E02** | `E02-T01` | Issue Tracker Adapters (`gh` & `glab`) | `⚡ Direct` | Subprocess CLI wrappers & hierarchy parsing. |
| | `E02-T02` | Heuristic Noise Sanitization Engine | `⚡ Direct` | Regex/markdown cleanup & trace stripping. |
| | `E02-T03` | DoD Extraction & `manifest/models.py` | `⚡ Direct` | Checklist extraction & Pydantic manifest models. |
| | `E02-T04` | Dependency DAG, Topological Sort & `TASKS.md` AST | `🧠 Deep Verify` | Test cycle detection & lossless `[x]`/`[BLOCKED]` preservation. |
| | `E02-T05` | `plan` CLI Subcommand & Dry-Run | `⚡ Direct` | Wire `plan` CLI + manifest unit tests. |
| **E03** | `E03-T01` | Context-Isolated Prompt Assembler & Effort | `⚡ Direct` | Clean prompt template + `--mode` / effort mapping. |
| | `E03-T02` | Headless `agy` Runner, Stdin Disconnect & Metering | `🧠 Deep Verify` | Test `DEVNULL` stdin, process-group timeout & token extraction. |
| | `E03-T03` | Pre-Flight Baseline Check, Verifier & Retry Loop | `⚡ Direct` | Shell verifier & bounded retry error injection. |
| | `E03-T04` | Autonomous Supervisor Loop (`run`), `--detach` & Reports | `🧠 Deep Verify` | Test mid-turn 429 recovery, `--detach` & token budget caps. |
| | `E03-T05` | Release Pipeline, Binaries & `package_plugin.py` | `⚡ Direct` | GitHub Actions release workflow & PyInstaller packaging. |
| **E04** | `E04-T01` | Global SQLite Process Registry (`registry.db`) | `🧠 Deep Verify` | Lock `registry.db` WAL contract, signals, `SessionState` & PID pruning. |
| | `E04-T02` | CLI Process Management (`ps`, `wake`, `stop`, `logs`) | `🐝 Parallel` | Build against locked `E04-T01` contract + signal tests. |
| | `E04-T03` | TypeScript + Alpine.js Web Cockpit & Trend Chart | `🐝 Parallel` | Build `ui/frontend/` + Trend Chart against API contract. |
| | `E04-T04` | Zero-Dependency Local HTTP Server (`dashboard`) | `🐝 Parallel` | Build stdlib `http.server` + REST endpoints + PyInstaller assets. |
| | `E04-T05` | Antigravity IDE UI Status Pill & Webview Pane | `⚡ Direct` | Wire IDE status pill to local dashboard. |

---

## 6. Standard Epic Kickoff Prompt (For Orchestrator Sessions)

Use this prompt whenever starting an Epic (`E0X`) on the Orchestrator model:

```text
Implement Epic E0X in roadmap/E0X/EPIC.md sequentially, following AGENT_ARCHITECTURE_PLAN.md.
- Read each task spec before coding and satisfy every acceptance criterion.
- Use Python 3.15 (pinned via .python-version), macOS/Linux only.
- For `⚡ Direct` tasks: implement and test directly in one pass.
- For `🧠 Deep Verify` tasks: add thorough edge-case/signal/concurrency tests and audit your diff against the spec.
- For `🐝 Parallel` tasks (E04-T02..T04): run parallel workers only after E04-T01 locks `registry.db` and `SessionState`.
- Run `task check` (or `uv run ruff check` + `uv run pytest`) after each task, commit GPG-signed (`fix: <issue-url>` in body), then:
  1. Mark `[x] DONE`, record `Implementing Commits (\`main\`)`, and check `[x]` on all AC / Verification / DoD checkboxes in local `roadmap/E0X/T0Y-*.md`, `roadmap/E0X/EPIC.md`, and `roadmap/ROADMAP.md`.
  2. Sync the updated body (preserving top GitHub callout headers) to the GitHub Task Issue via `gh issue edit <task-issue> --body-file ...` **before** calling `gh issue close`; once all tasks in `E0X` finish, sync the Epic body via `gh issue edit <epic-issue> --body-file ...` **before** closing the Epic issue.
- Keep the human global identity as Committer/GPG signer and `Alessandro Puccetti Agent [bot]` as Author on agent commits.
```
