# Task E03-T01: Context-Isolated Prompt Assembler & Reasoning Effort Profile Resolver

- **Task ID:** `E03-T01`
- **Parent Epic:** [Epic E03: Autonomous Execution Runner, Detached Supervisor & Telemetry (`run`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `core-developer`
- **Dependencies:** `E01-T01`, `E02-T04`

---

## 1. User Story & Strategic Value

> **As an** Orchestration Architect,  
> **I want** a prompt assembler that constructs high-signal, self-contained prompts containing only project architecture rules, isolated task specs, and verification criteria, paired with an effort policy resolver,  
> **So that** each task runs with zero historical context baggage while utilizing appropriate reasoning effort (`low`, `medium`, `high`) based on the task complexity.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** `src/agymissioncontrol/runner/prompt.py` formats prompt strings combining project rules (`AGENTS.md` / `CONVENTIONS.md` if present), task item scope, constraints, and verification criteria.
- [ ] **AC-2:** Prompt strictly instructs the agent to implement code edits and ensure the verification command exits with code `0`.
- [ ] **AC-3:** On retry attempts, appends the failure diagnostic trace (stdout + stderr) from previous verification runs.
- [ ] **AC-4:** `resolve_model_and_effort(task, config, attempt)` maps task complexity and phase to model and reasoning effort (`low`, `medium`, `high`, `xhigh`).
- [ ] **AC-5:** Unit tests in `tests/test_prompt.py` verify prompt structure, retry context appending, and effort resolution.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/runner/prompt.py` | Create | Prompt assembler, rule injector, and effort profile resolver |
| `tests/test_prompt.py` | Create | Unit tests validating prompt generation and effort mappings |

---

## 4. Technical Implementation Specification

### 4.1 Prompt Layout Invariants
```markdown
# TASK SPECIFICATION: {task.task_id} - {task.title} (Issue #{task.issue_number})

## Scope
{task.scope}

## Constraints
{task.constraints}

## Mandatory Verification
You MUST implement the code changes and verify your solution by running:
`{task.verification_command}`

The task is only successful if this verification command exits with code 0.
Do not modify unrelated files.

{error_feedback_if_retry}
```

### 4.2 Effort Resolution Matrix
- **Planning / Architecture:** Claude Sonnet or Gemini Pro on `effort: medium` / `high`.
- **Implementation (Attempt 1):** Configured primary model on `effort: medium`.
- **Implementation (Attempt 2-3 / Fix):** Configured primary model on `effort: high` with failure trace.
- **Verification / Linting / Simple Tasks:** Gemini Flash on `effort: low`.

---

## 5. Verification & Definition of Done

- [ ] Unit tests verify prompt correctly includes `AGENTS.md` content when file exists in root.
- [ ] Unit tests verify attempt 2 prompt includes exact verification error trace.
- [ ] `uv run pytest tests/test_prompt.py` passes 100%.
