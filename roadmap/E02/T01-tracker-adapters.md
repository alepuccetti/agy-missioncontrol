# Task E02-T01: Issue Tracker Client Adapters (`gh` & `glab` CLI with Parent/Sub-issue & `blocked by` Hierarchy Parsing)

- **Task ID:** `E02-T01`
- **Parent Epic:** [Epic E02: Auxiliary Bootstrap Tool: Issue-to-Task List Generator (`plan` / `TASKS.md`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `core-developer`
- **Dependencies:** `E01-T01`

---

## 1. User Story & Strategic Value

> **As an** Integration Engineer,  
> **I want** client adapters for GitHub (`gh`) and GitLab (`glab`) CLIs that parse raw issue payloads along with parent/child relationships and `blocked by #id` dependency annotations,  
> **So that** tickets and their implicit dependency hierarchy can be ingested seamlessly into the local planning pipeline.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** `src/agymissioncontrol/manifest/tracker/base.py` defines `RawIssue` model with fields `number`, `title`, `body`, `labels`, `milestone`, `parent_issue_number`, `blocked_by_numbers`.
- [ ] **AC-2:** `GitHubTracker` invokes `gh issue list --json number,title,body,labels,milestone` and extracts `Blocked by #<id>` or `- [ ] #<id>` tasklist patterns from issue bodies.
- [ ] **AC-3:** If GitHub sub-issues or project hierarchy fields are present, extracts `parent_issue_number`.
- [ ] **AC-4:** `GitLabTracker` invokes `glab issue list` with equivalent parsing of issue links.
- [ ] **AC-5:** `MockTracker` reads fixture JSON/YAML files containing pre-linked issue trees for offline testing.
- [ ] **AC-6:** Unit tests verify extraction of dependencies from both markdown tasklists and issue body regexes.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/manifest/tracker/base.py` | Create/Modify | `RawIssue` model with dependency references and `IssueTracker` ABC |
| `src/agymissioncontrol/manifest/tracker/github.py` | Create/Modify | GitHub CLI wrapper with `blocked by` & tasklist extraction |
| `src/agymissioncontrol/manifest/tracker/gitlab.py` | Create/Modify | GitLab CLI wrapper |
| `src/agymissioncontrol/manifest/tracker/mock.py` | Create/Modify | Mock tracker with multi-issue dependency fixtures |
| `tests/test_trackers.py` | Create/Modify | Unit tests mocking `gh` output with dependencies |

---

## 4. Technical Implementation Specification

### 4.1 Dependency Extraction Heuristics in Tracker
```python
import re

BLOCKED_BY_PATTERN = re.compile(r'(?:blocked by|depends on)\s+#?(\d+)', re.IGNORECASE)
TASKLIST_ISSUE_PATTERN = re.compile(r'-\s*\[\s*\]\s*#(\d+)')

def extract_issue_dependencies(body: str) -> List[int]:
    deps = set()
    for match in BLOCKED_BY_PATTERN.finditer(body):
        deps.add(int(match.group(1)))
    for match in TASKLIST_ISSUE_PATTERN.finditer(body):
        deps.add(int(match.group(1)))
    return sorted(list(deps))
```

---

## 5. Verification & Definition of Done

- [ ] Mock test verifying that an issue body with `Blocked by #42` extracts `blocked_by_numbers = [42]`.
- [ ] Mock test verifying that a tasklist with `- [ ] #101` extracts `blocked_by_numbers = [101]`.
- [ ] `uv run pytest tests/test_trackers.py` passes 100%.
