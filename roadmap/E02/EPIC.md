# Epic E02: Auxiliary Bootstrap Tool: Issue-to-Task List Generator (`plan` / `TASKS.md`)

- **Epic ID:** `E02`
- **Horizon:** Horizon 1 — Auxiliary Bootstrap Utilities
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Standard Teamwork
- **Primary Agents:** `core-developer`
- **Dependencies:** `E00` (Setup), `E01` (Config & CLI)
- **Specification References:** [Project Specification §3 (Core Architecture & Workflow)](../../README.md#3-core-architecture--workflow), [§4 (State & Concurrency: TASKS.md)](../../README.md#4-state--concurrency-hybrid-architecture), [§6 (CLI: plan)](../../README.md#6-cli-interface-specification)

---

## 1. Epic User Story & Strategic Value

> **As an** Orchestrator and Technical Lead,  
> **I want** an auxiliary bootstrap command `agymissioncontrol plan` that pulls remote tickets from GitHub/GitLab, strips conversational noise, extracts acceptance criteria, and compiles a structured task checklist (`TASKS.md`),  
> **So that** developers and agents have an optional, lightweight helper to scaffold backlogs into actionable tasks without requiring a separate tool or imposing rigid workflow constraints.

---

## 2. Epic Objective & Architectural Scope

Implement tracker integrations (`gh` and `glab`), an intelligent markdown/HTML sanitization engine, DoD and verification command extraction heuristics, dependency graph resolution (parsing GitHub `blocked by #id` and parent/sub-issue hierarchies), a lossless round-trip markdown AST parser and serializer for `TASKS.md`, and the `agymissioncontrol plan` auxiliary CLI command.

### Key Architectural Invariants Established in E02
1. **Auxiliary Sub-Tool Invariant:**
   - `agymissioncontrol plan` is an optional scaffolding utility.
   - The core Quota Governor (`gate`/`status`), Process Registry (`ps`/`wake`/`stop`), and Web Cockpit (`dashboard`) do NOT depend on `plan` or `TASKS.md` to function.
2. **Implicit & Explicit Dependency Graph:**
   - Detects remote task dependencies from GitHub tasklists (`- [ ] #101`, `Blocked by #101`) or parent/sub-issue links.
   - Suggests a safe topological execution order.
3. **Lossless Markdown Manifest:** Ingesting new tickets incrementally updates `TASKS.md` without overwriting completed items (`- [x]`), blocked items (`- [BLOCKED]`), or manual developer notes.
4. **Normalized Ticket Schema:**
   ```markdown
   - [ ] **TASK-101: Add In-Memory Rate Limiting Middleware** (Issue #101)
     - **Depends-On:** TASK-099
     - **Scope:** Implement token-bucket limiter in `src/middleware/rate_limit.py`.
     - **Constraints:** Must use Redis if available, fallback to local dict.
     - **Verification:** `pytest tests/unit/test_rate_limit.py && ruff check .`
   ```

---

## 3. Epic-Level Acceptance Criteria

- [ ] **AC-1 (Tracker Ingestion & Hierarchy):** `gh issue list` and `glab issue list` parse milestone, labels, issue bodies, parent issue links, and `blocked by #id` tasklists.
- [ ] **AC-2 (Sanitization Pipeline):** Cleans HTML comments, markdown badges, boilerplate templates, and oversized stack dumps.
- [ ] **AC-3 (DoD & Verification Extraction):** Identifies checkboxes, acceptance criteria, and explicit test commands; applies `--default-test` fallback.
- [ ] **AC-4 (Dependency Graph & Topological Sort):** Resolves dependency DAG; suggests safe execution order; detects cyclic dependencies.
- [ ] **AC-5 (Lossless AST Parser & Serializer):** Accurately parses and serializes `TASKS.md` with incremental merging (never resets `- [x]` or `- [BLOCKED]`).
- [ ] **AC-6 (`agymissioncontrol plan` CLI Command):** Exposes `--provider`, `--milestone`, `--label`, `--default-test`, `--output`, `--suggest-order`, and `--dry-run`.

---

## 4. Tasks in This Epic

| Task ID | Title | Signal | Status |
| :--- | :--- | :---: | :---: |
| **[E02-T01](./T01-tracker-adapters.md)** | Issue Tracker Client Adapters (`gh` & `glab` CLI with Parent/Sub-issue & `blocked by` Hierarchy Parsing) | ⚡ Direct | `[ ] TODO` |
| **[E02-T02](./T02-sanitization-pipeline.md)** | Heuristic Noise Sanitization Engine (Boilerplate, Badges, HTML Comments & Trace Stripping) | ⚡ Direct | `[ ] TODO` |
| **[E02-T03](./T03-dod-and-test-extractor.md)** | Acceptance Criteria Parsing, DoD Extraction & Verification Command Synthesizer | ⚡ Direct | `[ ] TODO` |
| **[E02-T04](./T04-manifest-parser-and-serializer.md)** | Dependency Graph Resolver, Topological Order Suggestion & `TASKS.md` AST Parser/Serializer | 🧠 Deep Verify | `[ ] TODO` |
| **[E02-T05](./T05-plan-cli-command.md)** | CLI Subcommand (`agymissioncontrol plan`) with Dependency Graph Display, Dry-Run Mode & Manifest Unit Tests | ⚡ Direct | `[ ] TODO` |

---

## 5. Epic Completion Gate (Definition of Done)

- [ ] Unit tests in `tests/test_sanitizer.py`, `tests/test_dependencies.py`, and `tests/test_manifest.py` pass with 100% assertions.
- [ ] Running `agymissioncontrol plan` correctly extracts remote `blocked by` links and emits `- **Depends-On:** TASK-XXX` in topological order.
- [ ] Incremental merge tests verify that completed tasks (`- [x]`) and blocked tasks (`- [BLOCKED]`) are never overwritten.
- [ ] `task check` executes cleanly (formatting, linting, tests).
