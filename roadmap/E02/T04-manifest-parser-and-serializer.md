# Task E02-T04: Dependency Graph Resolver, Topological Order Suggestion & `TASKS.md` AST Parser/Serializer

- **Task ID:** `E02-T04`
- **Parent Epic:** [Epic E02: Auxiliary Bootstrap Tool: Issue-to-Task List Generator (`plan` / `TASKS.md`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** 🧠 Deep Verify
- **Primary Agent:** `core-developer`
- **Dependencies:** `E02-T03`

---

## 1. User Story & Strategic Value

> **As an** Orchestration Architect,  
> **I want** a dependency graph resolver and topological order suggester integrated with the `TASKS.md` markdown AST parser and serializer,  
> **So that** tasks are sequenced in safe prerequisite order and downstream tasks know exactly which upstream items must succeed before they can run.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** `TaskItem` model supports optional field `depends_on: List[str] = []` (e.g. `["TASK-101"]`).
- [ ] **AC-2:** `DependencyResolver` performs topological sort on task lists, ordering prerequisites ahead of dependents.
- [ ] **AC-3:** Detects cyclic dependencies (e.g. A -> B -> A) and raises a clear diagnostic error with cycle path.
- [ ] **AC-4:** If no explicit dependencies exist, infers a suggested execution order based on issue numbers and shared module scope.
- [ ] **AC-5:** AST parser and serializer losslessly parse and emit `- **Depends-On:** TASK-XXX`.
- [ ] **AC-6:** Incremental merger preserves existing completed (`- [x]`) and blocked (`- [BLOCKED]`) tasks and their dependencies.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/manifest/models.py` | Modify | Add `depends_on` to `TaskItem` |
| `src/agymissioncontrol/manifest/dependencies.py` | Create | Topological sort, cycle detector, and heuristic order suggester |
| `src/agymissioncontrol/manifest/parser.py` | Create | Lossless `TASKS.md` AST parser, serializer, and incremental merger |
| `tests/test_dependencies.py` | Create | Unit tests for topological sorting, cycle detection, and order inference |
| `tests/test_manifest.py` | Create | Unit tests for lossless `TASKS.md` round-trip parsing and incremental merging |

---

## 4. Technical Implementation Specification

### 4.1 Manifest Format Invariant with Dependencies
```markdown
- [ ] **TASK-102: Implement Profile API Endpoints** (Issue #102)
  - **Depends-On:** TASK-101
  - **Scope:** Add GET/PUT `/api/v1/profile` in `src/api/profile.py`.
  - **Constraints:** Must use auth middleware from TASK-101.
  - **Verification:** `pytest tests/unit/test_profile.py && ruff check .`
  - **Git-Commit:** `feat(api): implement user profile endpoints (closes #102)`
```

### 4.2 Topological Sort Algorithm
- Construct directed adjacency graph $G = (V, E)$ where edge $(u, v)$ means $u$ must run before $v$.
- In-degree resolution with Kahn's algorithm or DFS-based post-order reversal.
- If cycle detected, raise `CyclicDependencyError("Cycle detected: TASK-A -> TASK-B -> TASK-A")`.

---

## 5. Verification & Definition of Done

- [ ] Unit test verifies topological sorting reorders dependent tasks correctly.
- [ ] Unit test verifies cyclic dependency raises `CyclicDependencyError`.
- [ ] Round-trip parsing test verifies `- **Depends-On:** TASK-101` is parsed and serialized losslessly.
- [ ] `uv run pytest tests/test_dependencies.py` passes 100%.
