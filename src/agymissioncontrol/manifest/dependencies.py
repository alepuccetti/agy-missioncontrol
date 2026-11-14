"""Dependency DAG resolver, cycle detector, and topological execution ordering."""

from __future__ import annotations

import heapq
import re
from collections import defaultdict
from collections.abc import Sequence

from agymissioncontrol.manifest.models import TaskItem

_MODULE_HINT_PATTERN = re.compile(
    r"(?:src/(?:agymissioncontrol/)?|tests/(?:unit/|integration/)?)([a-zA-Z0-9_]+)"
)
_COMMIT_SCOPE_PATTERN = re.compile(r"^[a-z]+\(([a-zA-Z0-9_-]+)\):")


class CyclicDependencyError(ValueError):
    """Raised when task dependencies contain a directed cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        path = " -> ".join(cycle)
        super().__init__(f"Cycle detected: {path}")


def _infer_module_scope(task: TaskItem) -> str:
    """Infer a module/area clustering key for heuristic tie-breaking."""
    if task.commit_message:
        commit_match = _COMMIT_SCOPE_PATTERN.match(task.commit_message.strip())
        if commit_match:
            scope = commit_match.group(1).lower()
            if scope != "core":
                return scope

    for text in (task.scope, task.verification, task.title):
        if not text:
            continue
        mod_match = _MODULE_HINT_PATTERN.search(text)
        if mod_match:
            candidate = mod_match.group(1).lower()
            if candidate.startswith("test_"):
                candidate = candidate.removeprefix("test_").removesuffix(".py")
            return candidate

    return "core"


def _numeric_sort_key(task: TaskItem) -> tuple[int, str]:
    if task.issue_number is not None:
        return (task.issue_number, task.task_id)
    digits = re.search(r"(\d+)$", task.task_id)
    if digits:
        return (int(digits.group(1)), task.task_id)
    return (10**9, task.task_id)


class DependencyResolver:
    """Resolves task dependency DAGs into a safe topological execution sequence."""

    def resolve(
        self,
        tasks: Sequence[TaskItem],
        *,
        suggest_order: bool = True,
    ) -> list[TaskItem]:
        """Sort tasks topologically (prerequisites before dependents), rejecting cycles."""
        if not tasks:
            return []

        by_id: dict[str, TaskItem] = {}
        index_by_id: dict[str, int] = {}
        for idx, task in enumerate(tasks):
            if task.task_id in by_id:
                raise ValueError(f"Duplicate task ID in manifest: {task.task_id}")
            by_id[task.task_id] = task
            index_by_id[task.task_id] = idx

        # Build prerequisite -> dependent adjacency list over tasks present in this batch.
        dependents: dict[str, list[str]] = defaultdict(list)
        in_degree: dict[str, int] = {task.task_id: 0 for task in tasks}

        for task in tasks:
            seen_deps: set[str] = set()
            for dep_id in task.depends_on:
                if dep_id == task.task_id:
                    raise CyclicDependencyError([task.task_id, task.task_id])
                if dep_id in by_id and dep_id not in seen_deps:
                    seen_deps.add(dep_id)
                    dependents[dep_id].append(task.task_id)
                    in_degree[task.task_id] += 1

        self._detect_cycles(tasks, by_id, dependents)

        module_rank = self._compute_module_ranks(tasks) if suggest_order else {}

        def _priority_key(task_id: str) -> tuple[object, ...]:
            task = by_id[task_id]
            if not suggest_order:
                return (index_by_id[task_id], task_id)
            mod = _infer_module_scope(task)
            num_key = _numeric_sort_key(task)
            return (module_rank[mod], num_key, task_id)

        ready_heap: list[tuple[tuple[object, ...], str]] = []
        for task in tasks:
            if in_degree[task.task_id] == 0:
                heapq.heappush(ready_heap, (_priority_key(task.task_id), task.task_id))

        ordered: list[TaskItem] = []
        while ready_heap:
            _, current_id = heapq.heappop(ready_heap)
            ordered.append(by_id[current_id])
            for next_id in dependents.get(current_id, ()):
                in_degree[next_id] -= 1
                if in_degree[next_id] == 0:
                    heapq.heappush(ready_heap, (_priority_key(next_id), next_id))

        return ordered

    def topological_sort(
        self,
        tasks: Sequence[TaskItem],
        *,
        suggest_order: bool = True,
    ) -> list[TaskItem]:
        """Alias for `resolve()`."""
        return self.resolve(tasks, suggest_order=suggest_order)

    @staticmethod
    def _compute_module_ranks(tasks: Sequence[TaskItem]) -> dict[str, tuple[int, str]]:
        ranks: dict[str, tuple[int, str]] = {}
        for task in tasks:
            mod = _infer_module_scope(task)
            key = _numeric_sort_key(task)
            if mod not in ranks or key < ranks[mod]:
                ranks[mod] = key
        return ranks

    @staticmethod
    def _detect_cycles(
        tasks: Sequence[TaskItem],
        by_id: dict[str, TaskItem],
        dependents: dict[str, list[str]],
    ) -> None:
        # 0 = unvisited, 1 = visiting (on stack), 2 = visited
        state: dict[str, int] = {task.task_id: 0 for task in tasks}
        stack: list[str] = []
        pos_in_stack: dict[str, int] = {}

        ordered_ids = [t.task_id for t in sorted(tasks, key=_numeric_sort_key)]

        def _dfs(node_id: str) -> None:
            state[node_id] = 1
            pos_in_stack[node_id] = len(stack)
            stack.append(node_id)

            neighbors = sorted(
                dependents.get(node_id, ()),
                key=lambda tid: _numeric_sort_key(by_id[tid]),
            )
            for nxt in neighbors:
                nxt_state = state[nxt]
                if nxt_state == 1:
                    cycle_start = pos_in_stack[nxt]
                    cycle_path = stack[cycle_start:] + [nxt]
                    raise CyclicDependencyError(cycle_path)
                if nxt_state == 0:
                    _dfs(nxt)

            stack.pop()
            pos_in_stack.pop(node_id, None)
            state[node_id] = 2

        for start_id in ordered_ids:
            if state[start_id] == 0:
                _dfs(start_id)
