# Task E02-T02: Heuristic Noise Sanitization Engine (Boilerplate, Badges, HTML Comments & Trace Stripping)

- **Task ID:** `E02-T02`
- **Parent Epic:** [Epic E02: Auxiliary Bootstrap Tool: Issue-to-Task List Generator (`plan` / `TASKS.md`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `core-developer`
- **Dependencies:** `E02-T01`

---

## 1. User Story & Strategic Value

> **As an** Orchestration Architect,  
> **I want** a regex and AST-based sanitization pipeline that cleans ticket descriptions of noisy markdown badges, HTML comments, boilerplate templates, and oversized stack dumps,  
> **So that** subsequent agent prompt injections remain lightweight and high-signal, preventing context window ballooning and hallucination drift.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** `src/agymissioncontrol/manifest/sanitizer.py` implements `sanitize_issue_body(raw_body: str) -> str`.
- [ ] **AC-2:** Strips HTML comments (`<!-- ... -->`) and empty markdown headers.
- [ ] **AC-3:** Strips markdown image badges (such as shield.io or CI status badges).
- [ ] **AC-4:** Removes boilerplate template sections (e.g. `### Environment`, `### Steps to reproduce` if purely filled with placeholder text like `None` or `N/A`).
- [ ] **AC-5:** Truncates raw terminal crash dumps over 40 lines into a concise 10-line summary with indicator `[... truncated log ...]`.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/manifest/sanitizer.py` | Create | Regex patterns, HTML/Markdown filters, and log truncation heuristics |
| `tests/test_sanitizer.py` | Create | Unit tests validating dirty issue bodies against expected clean output |

---

## 4. Technical Implementation Specification

### 4.1 Sanitization Heuristic Stages
1. **Comment Elimination:** `re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)`
2. **Badge Elimination:** `re.sub(r'\[\!\[.*?\]\(.*?\)\]\(.*?\)', '', text)`
3. **Template Boilerplate Stripping:** Detect standard issue templates (e.g., headers followed by checkboxes or placeholder text).
4. **Log Block Truncation:** Fenced code blocks (` ``` ... ``` `) exceeding 40 lines are truncated to head 5 + tail 5 lines.

---

## 5. Verification & Definition of Done

- [ ] Test cases in `tests/test_sanitizer.py` with multi-paragraph issue templates verify all badges and comments are stripped.
- [ ] Verified that legitimate code snippets, file references, and requirements are preserved intact.
- [ ] `uv run pytest tests/test_sanitizer.py` passes 100%.
