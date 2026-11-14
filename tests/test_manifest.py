"""Adversarial & round-trip tests for `TASKS.md` parser, serializer, and merger (`E02-T04`)."""

from __future__ import annotations

import pytest

from agymissioncontrol.manifest.dependencies import CyclicDependencyError
from agymissioncontrol.manifest.models import TaskItem, TaskStatus
from agymissioncontrol.manifest.parser import (
    merge_manifests,
    parse_manifest,
    parse_manifest_document,
    serialize_manifest,
)

SAMPLE_MANIFEST = """# Execution Task Manifest (`TASKS.md`)

> Hand-maintained header note.

- [x] **TASK-101: Add Quota Telemetry (V2)** (Issue #101)
  - **Depends-On:** TASK-099
  - **Scope:** Implement `src/agymissioncontrol/quota/provider.py`.
  - **Constraints:** Must stay offline-safe under mock mode.
  - **Acceptance-Criteria:**
    - Parse quota groups cleanly
    - Record snapshots in SQLite WAL
  - **Verification:** `pytest tests/test_quota_providers.py`
  - **Git-Commit:** `feat(quota): add quota telemetry (closes #101)`
  - **Manual-Note:** Developer verified WAL lock recovery manually.

- [BLOCKED] **TASK-102: Implement Profile API Endpoints** (Issue #102)
  - **Depends-On:** TASK-101, TASK-100
  - **Scope:** Add GET/PUT `/api/v1/profile` in `src/api/profile.py`.
  - **Constraints:** Must use auth middleware from TASK-101.
  - **Verification:** `pytest tests/unit/test_profile.py && ruff check .`
  - **Git-Commit:** `feat(api): implement user profile endpoints (closes #102)`

- [ ] **TASK-103: Add CLI Status Table** (Issue #103)
  - **Depends-On:** TASK-101
  - **Scope:** Render Rich table in `src/agymissioncontrol/cli.py`.
  - **Verification:** `pytest tests/test_cli_gate_status.py`
  - **Git-Commit:** `feat(cli): add CLI status table (closes #103)`
"""


def test_lossless_round_trip_parse_and_serialize() -> None:
    preamble, parsed = parse_manifest_document(SAMPLE_MANIFEST)
    assert "Hand-maintained header note." in preamble
    assert len(parsed) == 3

    t1, t2, t3 = parsed
    assert t1.status == TaskStatus.DONE
    assert t1.issue_number == 101
    assert t1.title == "Add Quota Telemetry (V2)"
    assert t1.depends_on == ["TASK-099"]
    assert t1.acceptance_criteria == [
        "Parse quota groups cleanly",
        "Record snapshots in SQLite WAL",
    ]
    assert t1.extra_lines == ["  - **Manual-Note:** Developer verified WAL lock recovery manually."]

    assert t2.status == TaskStatus.BLOCKED
    assert t2.depends_on == ["TASK-101", "TASK-100"]
    assert t2.verification == "pytest tests/unit/test_profile.py && ruff check ."

    assert t3.status == TaskStatus.TODO

    reserialized = serialize_manifest(parsed, header=preamble)
    reparsed = parse_manifest(reserialized)
    assert reparsed == parsed


def test_nested_checkbox_with_colon_does_not_split_task_block() -> None:
    content = """
- [ ] **TASK-201: Parser Robustness** (Issue #201)
  - **Acceptance-Criteria:**
    - [ ] Check: colon inside nested checkbox is preserved
  - **Verification:** `pytest tests/test_manifest.py`
"""
    tasks = parse_manifest(content)
    assert len(tasks) == 1
    assert tasks[0].acceptance_criteria == ["Check: colon inside nested checkbox is preserved"]
    assert tasks[0].verification == "pytest tests/test_manifest.py"


def test_unbolded_task_header_and_malformed_lines_handled_gracefully() -> None:
    assert parse_manifest("") == []
    assert parse_manifest("   \n# Just a heading\nRandom prose.\n") == []

    partial = """
# Custom Header
Random unindented commentary before tasks.

* [ ] TASK-55: Plain unbolded header (Issue #55)
  - **Depends-On:** None
  - **Scope:** Minimal spec without commit field
  - Developer freeform note

Unindented trailing paragraph that shouldn't merge into TASK-55.
"""
    tasks = parse_manifest(partial)
    assert len(tasks) == 1
    assert tasks[0].task_id == "TASK-55"
    assert tasks[0].issue_number == 55
    assert tasks[0].title == "Plain unbolded header"
    assert tasks[0].depends_on == []
    assert tasks[0].extra_lines == ["  - Developer freeform note"]


def test_incremental_merge_preserves_done_blocked_and_notes_and_orders_dag() -> None:
    existing = parse_manifest(SAMPLE_MANIFEST)
    incoming = [
        # Upstream ticket #101 edited on GitHub -> MUST NOT overwrite DONE status or notes!
        TaskItem(
            task_id="TASK-101",
            issue_number=101,
            title="Rewritten Remote Title",
            status=TaskStatus.TODO,
            depends_on=["TASK-098"],
            scope="Overwritten scope",
            verification="pytest",
        ),
        # Upstream ticket #102 edited -> MUST NOT overwrite BLOCKED status or existing deps!
        TaskItem(
            task_id="TASK-102",
            issue_number=102,
            title="New Profile Title",
            status=TaskStatus.TODO,
            depends_on=[],
            verification="pytest",
        ),
        # Open ticket #103 adds a newly discovered upstream blocker TASK-102
        TaskItem(
            task_id="TASK-103",
            issue_number=103,
            title="Add Enhanced CLI Status Table",
            depends_on=["TASK-102"],
            scope="Render Rich table and JSON in `src/agymissioncontrol/cli.py`.",
            verification="pytest tests/test_cli_gate_status.py",
        ),
        # Brand new ticket #104 depending on #103
        TaskItem(
            task_id="TASK-104",
            issue_number=104,
            title="Add Shell Completions",
            depends_on=["TASK-103"],
            scope="src/agymissioncontrol/cli.py",
            verification="pytest tests/test_cli_plan.py",
        ),
    ]

    merged = merge_manifests(existing, incoming)
    by_id = {t.task_id: t for t in merged}

    # DONE and BLOCKED tasks remain intact
    assert by_id["TASK-101"].status == TaskStatus.DONE
    assert by_id["TASK-101"].title == "Add Quota Telemetry (V2)"
    assert by_id["TASK-101"].extra_lines == [
        "  - **Manual-Note:** Developer verified WAL lock recovery manually."
    ]

    assert by_id["TASK-102"].status == TaskStatus.BLOCKED
    assert by_id["TASK-102"].depends_on == ["TASK-101", "TASK-100"]

    # TODO task updates title/scope and retains union of dependencies
    assert by_id["TASK-103"].status == TaskStatus.TODO
    assert by_id["TASK-103"].title == "Add Enhanced CLI Status Table"
    assert by_id["TASK-103"].depends_on == ["TASK-101", "TASK-102"]

    # Topological order respected across merged DAG: 101 -> 102 -> 103 -> 104
    ordered_ids = [t.task_id for t in merged]
    assert ordered_ids.index("TASK-101") < ordered_ids.index("TASK-102")
    assert ordered_ids.index("TASK-102") < ordered_ids.index("TASK-103")
    assert ordered_ids.index("TASK-103") < ordered_ids.index("TASK-104")


def test_incremental_merge_raises_on_cycle() -> None:
    existing = [
        TaskItem(task_id="TASK-101", issue_number=101, title="A", depends_on=["TASK-102"]),
    ]
    incoming = [
        TaskItem(task_id="TASK-102", issue_number=102, title="B", depends_on=["TASK-101"]),
    ]
    with pytest.raises(CyclicDependencyError, match="Cycle detected"):
        merge_manifests(existing, incoming)
