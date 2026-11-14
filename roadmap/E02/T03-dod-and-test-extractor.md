# Task E02-T03: Acceptance Criteria Parsing, DoD Extraction & Verification Command Synthesizer

- **Task ID:** `E02-T03`
- **Parent Epic:** [Epic E02: Auxiliary Bootstrap Tool: Issue-to-Task List Generator (`plan` / `TASKS.md`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `core-developer`
- **Dependencies:** `E02-T02`

---

## 1. User Story & Strategic Value

> **As an** Autonomous Developer,  
> **I want** an extractor that identifies Acceptance Criteria, Definition of Done, and explicit shell test commands from issues,  
> **So that** every compiled task includes an unambiguous, executable verification criteria rather than requiring the agent to guess how to verify its work.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** `src/agymissioncontrol/manifest/extractor.py` implements `extract_task_spec(sanitized_body, title, issue_num, default_test)`.
- [ ] **AC-2:** Extracts explicit verification commands found in code blocks, inline backticks, or marked with `Verification:` / `Test:` keywords.
- [ ] **AC-3:** If no verification command is found, searches for modified test files (e.g., `tests/test_foo.py`) to synthesize `pytest tests/test_foo.py`.
- [ ] **AC-4:** If no test files are mentioned, applies the configured `default_test` command (e.g. `pytest` or `cargo test`).
- [ ] **AC-5:** Synthesizes conventional git commit message format: `feat(<scope>): <title> (closes #<number>)`.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/manifest/models.py` | Create | `TaskItem` and `TaskStatus` domain models |
| `src/agymissioncontrol/manifest/extractor.py` | Create | DoD extraction logic, regex command parsers, scope identifier, and commit builder |
| `tests/test_extractor.py` | Create | Unit tests validating test extraction heuristics and fallback logic |

---

## 4. Technical Implementation Specification

### 4.1 Extraction Heuristics
1. **Verification Command Discovery:**
   - Scan for patterns like:
     - `- \*\*Verification:\*\* \`([^\`]+)\``
     - `Verification command:\s*\`([^\`]+)\``
     - Code blocks with language `bash` or `sh` following a "Testing" or "Verification" header.
2. **Commit Convention:**
   - Detect scope from issue labels (e.g. `area/quota` -> `feat(quota): ...`) or file mentions.
   - Format: `<type>(<scope>): <summary> (closes #<issue_num>)`.

---

## 5. Verification & Definition of Done

- [ ] Unit tests verify extraction of explicit verification commands from issue bodies.
- [ ] Unit tests verify fallback to `--default-test` when issue body contains no test commands.
- [ ] `uv run pytest tests/test_extractor.py` passes 100%.
