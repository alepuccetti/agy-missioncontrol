# Epic E00: Workspace Permissions, Command Guards, Python Tooling & Repository Setup

- **Epic ID:** `E00`
- **Horizon:** Horizon 0 — Pre-Implementation Setup (Release Milestone `v0.1.0-alpha`)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Standard Teamwork
- **Primary Agents:** `infra-specialist` (or Primary Orchestrator)
- **Dependencies:** None (Pre-Flight Epic)
- **Specification References:** [Project Specification §1 (Executive Summary)](../../README.md#1-executive-summary--purpose), [§4 (State & Concurrency)](../../README.md#4-state--concurrency-hybrid-architecture)

---

## 1. Epic User Story & Strategic Value

> **As a** Lead Software Architect and Multi-Agent Orchestrator,  
> **I want** a secure, reproducible local Python 3.15 execution environment with zero-trust pre-tool command guardrails, strict Git tracking, standardized `go-task` development automation (`Taskfile.yml`), editable CLI installation (`task dev:link`), local Antigravity plugin linking, and standalone single-binary compilation (`task build:binary`),  
> **So that** subsequent autonomous agent runs and developers can build, lint, test, run live-reload CLI commands, and package standalone binaries without unauthorized network side-effects or environment drift.

---

## 2. Epic Objective & Architectural Scope

Establish the initial Git repository baseline, Python packaging foundation (`pyproject.toml` with `uv` and `hatchling`), pre-tool command interception hooks (`.agents/hooks.json` & `command_guard.py`), development workflow automation (`Taskfile.yml` with `dev:link`, `build:binary`, `ci`), local Antigravity plugin scaffolding (`plugin/`), and GitHub CLI linkage before functional module development begins in `E01`.

### Key Architectural Invariants Established in E00
1. **Granular Pre-Tool Safety Guardrail & Remote Agent Compatibility:**  
   `.agents/hooks.json` and `plugin/hooks.json` intercept shell execution via `command_guard.py` (relative `scripts/command_guard.py` inside `plugin/`) to auto-allow safe local Git inspections (`status`, `diff`, `log`, `branch`), builds, tests, formatting, and `task *` commands (`task test`, `task check`, `task lint`), while requiring human authorization on destructive file removal outside workspace, remote pushes (`git push --force`), or external credential access across both local developer laptops and headless Antigravity Remote Agent cloud VMs.
2. **Deterministic Python 3.15 Environment & Live Reload:**  
   Standardized `pyproject.toml` and `.python-version` (`3.15`) configuring `hatchling`, modern dependency pinning with `uv`, Ruff lint/format rules, Pytest coverage gates, and `uv tool install --editable .` ensuring edits in `src/agymissioncontrol/` are live instantly across the system.
3. **Reproducible Go-Task Automation & Plugin Linking:**  
   A centralized `Taskfile.yml` establishing clean targets: `task dev:link` (installs editable CLI and symlinks `plugin/` into `~/.gemini/config/plugins/agymissioncontrol`), `task build:binary` (single-file PyInstaller binary), `task ci`, `task check`, `task test`, and `task lint`.
4. **Clean Baseline & Origin Link:**  
   Git initialization on branch `main`, comprehensive `.gitignore` (ignoring `.venv/`, `dist/`, `.agymissioncontrol/`, `__pycache__/`, `.pytest_cache/`), and GitHub CLI remote repository validation.

---

## 3. Epic-Level Acceptance Criteria

- [x] **AC-1 (Git Workspace Baseline):** Git is initialized on default branch `main`, with a root `.gitignore` protecting build artifacts, virtual environments, and local test run outputs.
- [x] **AC-2 (Modern Python Packaging with `uv`):** `pyproject.toml` and `.python-version` configure `hatchling` backend, Python `>=3.15`, core dependencies (`typer>=0.29.0`, `pydantic>=2.14.0`, `pyyaml>=6.0.3`, `rich>=15.0.0`), dev tools (`pytest>=9.2.0`, `pytest-mock>=3.17.0`, `ruff>=0.17.0`, `pyinstaller>=6.22.0`), and entrypoint `agymissioncontrol`.
- [x] **AC-3 (Command Guard & Antigravity Hook):** `.agents/hooks.json`, `plugin/hooks.json`, `plugin/plugin.json`, and `.agents/scripts/command_guard.py` are active, providing deterministic pre-execution validation for `run_command` invocations across macOS and Linux (including Remote Agent cloud environments).
- [x] **AC-4 (Go-Task Automation & Pinned LTS CI):** `Taskfile.yml` provides operational targets `dev:link` (editable tool install + plugin symlink), `build:binary` (standalone executable), and `ci`. GitHub Actions `.github/workflows/ci.yml` matrix pipeline runs across pinned supported runners (`ubuntu-24.04`, `macos-15`) with pinned action versions, rejecting floating `-latest` aliases.
- [x] **AC-5 (GitHub CLI & Remote Readiness):** `gh auth status` verifies active credentials, and remote repository wiring is prepared for issue tracking and milestone synchronization.

---

## 4. Tasks in This Epic

| Task ID | Title | Signal | Status |
| :--- | :--- | :---: | :---: |
| **[E00-T01](./T01-git-init-and-ignore.md)** | Git Repository Initialization, MIT License, Branch Protection Baseline & `.gitignore` | ⚡ Direct | `[x] DONE` |
| **[E00-T02](./T02-python-packaging-and-uv-setup.md)** | Modern Python Packaging (`pyproject.toml`), Dependency Pinning & `uv` Toolchain | ⚡ Direct | `[x] DONE` |
| **[E00-T03](./T03-antigravity-hooks-and-command-guard.md)** | Antigravity Pre-Tool Hooks (`.agents/hooks.json`), `plugin.json` & `command_guard.py` | 🧠 Deep Verify | `[x] DONE` |
| **[E00-T04](./T04-dev-automation-taskfile.md)** | Developer Automation (`Taskfile.yml`) & Pinned LTS GitHub Actions CI Workflow | ⚡ Direct | `[x] DONE` |
| **[E00-T05](./T05-github-remote-link-and-labels.md)** | GitHub CLI Integration, Remote Origin Setup & Standard Issue Label Taxonomy | ⚡ Direct | `[x] DONE` |

---

## 5. Epic Completion Gate (Definition of Done)

- [x] Git repository initialized on `main` with initial commit containing project layout.
- [x] `pyproject.toml` passes syntax and dependency validation; `uv run ruff check` runs without errors.
- [x] `task dev:link` executes cleanly, installing `agymissioncontrol` into PATH and symlinking `plugin/`.
- [x] Command guard unit tests pass (`uv run pytest tests/test_command_guard.py`).
- [x] `task check` executes formatting checks, linting, and unit tests cleanly.
- [x] GitHub remote is linked or authenticated via `gh`.
