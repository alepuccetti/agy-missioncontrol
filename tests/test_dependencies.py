"""Adversarial & edge-case tests for dependency graph resolution (`E02-T04`)."""

from __future__ import annotations

import pytest

from agymissioncontrol.manifest.dependencies import CyclicDependencyError, DependencyResolver
from agymissioncontrol.manifest.models import TaskItem


def _make_task(
    task_id: str,
    issue_number: int,
    *,
    depends_on: list[str] | None = None,
    scope: str = "",
    commit_message: str = "",
) -> TaskItem:
    return TaskItem(
        task_id=task_id,
        issue_number=issue_number,
        title=f"Work for {task_id}",
        depends_on=depends_on or [],
        scope=scope,
        commit_message=commit_message,
    )


def test_empty_and_reverse_chain_topological_sort() -> None:
    resolver = DependencyResolver()
    assert resolver.resolve([]) == []

    tasks = [
        _make_task("TASK-103", 103, depends_on=["TASK-102"]),
        _make_task("TASK-102", 102, depends_on=["TASK-101"]),
        _make_task("TASK-101", 101),
    ]
    ordered = resolver.topological_sort(tasks)
    assert [t.task_id for t in ordered] == ["TASK-101", "TASK-102", "TASK-103"]


def test_diamond_dag_and_external_dependency_preserved() -> None:
    tasks = [
        _make_task("TASK-D", 104, depends_on=["TASK-B", "TASK-C"]),
        _make_task("TASK-C", 103, depends_on=["TASK-A"]),
        _make_task("TASK-B", 102, depends_on=["TASK-A"]),
        _make_task("TASK-A", 101, depends_on=["TASK-099"]),  # external prerequisite
    ]
    ordered = DependencyResolver().resolve(tasks)
    ids = [t.task_id for t in ordered]
    assert ids[0] == "TASK-A"
    assert ids[-1] == "TASK-D"
    assert set(ids[1:3]) == {"TASK-B", "TASK-C"}
    assert ordered[0].depends_on == ["TASK-099"]


def test_detects_self_loop_and_two_node_and_three_node_cycles() -> None:
    resolver = DependencyResolver()

    # Self-loop
    with pytest.raises(CyclicDependencyError, match="Cycle detected: TASK-A -> TASK-A"):
        resolver.resolve([_make_task("TASK-A", 1, depends_on=["TASK-A"])])

    # 2-node cycle
    with pytest.raises(CyclicDependencyError, match="Cycle detected: TASK-A -> TASK-B -> TASK-A"):
        resolver.resolve(
            [
                _make_task("TASK-A", 1, depends_on=["TASK-B"]),
                _make_task("TASK-B", 2, depends_on=["TASK-A"]),
            ]
        )

    # 3-node cycle embedded with acyclic tasks
    with pytest.raises(CyclicDependencyError) as exc_info:
        resolver.resolve(
            [
                _make_task("TASK-0", 10),
                _make_task("TASK-1", 11, depends_on=["TASK-3"]),
                _make_task("TASK-2", 12, depends_on=["TASK-1"]),
                _make_task("TASK-3", 13, depends_on=["TASK-2"]),
                _make_task("TASK-4", 14, depends_on=["TASK-3"]),
            ]
        )
    message = str(exc_info.value)
    assert message.startswith("Cycle detected: ")
    assert exc_info.value.cycle == ["TASK-1", "TASK-2", "TASK-3", "TASK-1"]


def test_heuristic_order_clusters_shared_module_scope_when_suggest_order_enabled() -> None:
    tasks = [
        _make_task("TASK-103", 103, scope="Update `src/agymissioncontrol/quota/ledger.py`"),
        _make_task("TASK-102", 102, scope="Update `src/agymissioncontrol/ui/display.py`"),
        _make_task("TASK-101", 101, scope="Update `src/agymissioncontrol/quota/governor.py`"),
    ]
    suggested = DependencyResolver().resolve(tasks, suggest_order=True)
    # `quota` shares earliest ticket (#101), so both quota tasks cluster before `ui` (#102)
    assert [t.task_id for t in suggested] == ["TASK-101", "TASK-103", "TASK-102"]

    unreordered = DependencyResolver().resolve(tasks, suggest_order=False)
    assert [t.task_id for t in unreordered] == ["TASK-103", "TASK-102", "TASK-101"]


def test_duplicate_task_id_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate task ID"):
        DependencyResolver().resolve(
            [
                _make_task("TASK-101", 101),
                _make_task("TASK-101", 101),
            ]
        )
