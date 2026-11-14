# Task E02-T05: CLI Subcommand (`agymissioncontrol plan`) with Dependency Graph Display, Dry-Run Mode & Manifest Unit Tests

- **Task ID:** `E02-T05`
- **Parent Epic:** [Epic E02: Auxiliary Bootstrap Tool: Issue-to-Task List Generator (`plan` / `TASKS.md`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `core-developer`
- **Dependencies:** `E02-T04`

---

## 1. User Story & Strategic Value

> **As an** Orchestration Engineer,  
> **I want** a unified CLI command `agymissioncontrol plan` that compiles issues into `TASKS.md`, displays the dependency DAG in a Rich tree or table, and supports dry-run preview,  
> **So that** I can inspect the planned execution topology before committing it to disk.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** Command `agymissioncontrol plan` accepts flags: `--issue`, `--provider`, `--milestone`, `--label`, `--default-test`, `--output`, `--suggest-order`, `--dry-run`.
- [ ] **AC-2:** Coordinates Tracker Ingestion ➔ Sanitization ➔ DoD Extraction ➔ Dependency Sorting ➔ Manifest Merging ➔ File Write.
- [ ] **AC-3:** Renders a Rich Tree or Table visualizing task execution order and upstream prerequisites (`Depends-On`).
- [ ] **AC-4:** If `--dry-run` is active, displays the generated/merged markdown without writing to disk.
- [ ] **AC-5:** Full CLI runner integration tests in `tests/test_cli_plan.py`.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/cli.py` | Modify | Register `plan` command with dependency options in Typer app |
| `tests/test_cli_plan.py` | Create/Modify | Typer `CliRunner` tests for `plan` command |

---

## 4. Technical Implementation Specification

### 4.1 CLI Output Rich Tree Example
```text
Task Execution Plan (4 tasks):
├── 1. TASK-100: Database Schema Migration
├── 2. TASK-101: In-Memory Rate Limiting (Depends on: TASK-100)
├── 3. TASK-102: User Profile API (Depends on: TASK-101)
└── 4. TASK-103: Documentation & Readme Updates (Independent)
```

---

## 5. Verification & Definition of Done

- [ ] Run `uv run agymissioncontrol plan --dry-run` and inspect terminal tree preview.
- [ ] Run `uv run agymissioncontrol plan --output /tmp/test_tasks.md` and verify file creation.
- [ ] Run `uv run pytest tests/test_cli_plan.py` with 100% assertions passing.
