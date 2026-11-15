"""Lossless AST parser, serializer, and incremental merger for `TASKS.md`."""

from __future__ import annotations

import re
from collections.abc import Sequence

from agymissioncontrol.manifest.dependencies import DependencyResolver
from agymissioncontrol.manifest.models import TaskItem, TaskStatus

DEFAULT_MANIFEST_HEADER = "# Execution Task Manifest (`TASKS.md`)\n"

_TASK_HEADER_PATTERN = re.compile(
    r"^[-*]\s*\[(?P<status>[^\]]*)\]\s+"
    r"(?:\*\*)?(?P<task_id>[A-Za-z0-9_.-]+):\s*(?P<title>.+?)(?:\*\*)?"
    r"(?:\s*\(Issue\s+#(?P<issue>\d+)\))?\s*$"
)
_TASK_HEADER_BOLD_WITH_TRAILING_ISSUE = re.compile(
    r"^[-*]\s*\[(?P<status>[^\]]*)\]\s+"
    r"\*\*(?P<task_id>[A-Za-z0-9_.-]+):\s*(?P<title>.+?)\*\*"
    r"(?:\s*\(Issue\s+#(?P<issue>\d+)\))?\s*$"
)
_SUBFIELD_PATTERN = re.compile(
    r"^\s{2,8}[-*]\s*(?:\*{1,2})?(?P<key>[A-Za-z][A-Za-z0-9 -]*?)"
    r"(?:\*{1,2})?:(?:\*{1,2})?\s*(?P<val>.*)$"
)
_NESTED_AC_ITEM_PATTERN = re.compile(r"^\s{4,12}[-*]\s*(?:\[[ xX]?\]\s*)?(?P<item>.+)$")


def _strip_wrapping_backticks(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped.startswith("`") and stripped.endswith("`"):
        return stripped[1:-1].strip()
    return stripped


def _parse_task_header(line: str) -> tuple[TaskStatus, str, str, int | None] | None:
    match = _TASK_HEADER_BOLD_WITH_TRAILING_ISSUE.match(line) or _TASK_HEADER_PATTERN.match(line)
    if not match:
        return None
    raw_status = match.group("status")
    status = TaskStatus.from_marker(raw_status)
    task_id = match.group("task_id").strip()
    title = match.group("title").strip().rstrip("*")
    raw_issue = match.group("issue")
    issue_number = int(raw_issue) if raw_issue else None
    if issue_number is None:
        id_num = re.search(r"-(\d+)$", task_id)
        if id_num:
            issue_number = int(id_num.group(1))
    return status, task_id, title, issue_number


def parse_manifest_document(content: str) -> tuple[str, list[TaskItem]]:
    """Parse `TASKS.md` into preserved leading preamble and structured `TaskItem` objects."""
    if not content or not content.strip():
        return ("", [])

    lines = content.splitlines()
    preamble_lines: list[str] = []
    tasks: list[TaskItem] = []

    current: TaskItem | None = None
    in_ac_list = False

    for line in lines:
        parsed_header = _parse_task_header(line)
        if parsed_header is not None:
            if current is not None:
                tasks.append(current)
            status, task_id, title, issue_number = parsed_header
            current = TaskItem(
                task_id=task_id,
                issue_number=issue_number,
                title=title,
                status=status,
            )
            in_ac_list = False
            continue

        if current is None:
            preamble_lines.append(line)
            continue

        # Blank line inside/between task blocks ends nested AC block cleanly
        if not line.strip():
            in_ac_list = False
            continue

        # Indented child metadata or manual notes belonging to `current`
        if line.startswith((" ", "\t")):
            if in_ac_list:
                ac_match = _NESTED_AC_ITEM_PATTERN.match(line)
                if ac_match and not _SUBFIELD_PATTERN.match(line):
                    item_text = ac_match.group("item").strip()
                    if item_text:
                        current.acceptance_criteria.append(item_text)
                    continue
                in_ac_list = False

            sub_match = _SUBFIELD_PATTERN.match(line)
            if sub_match:
                key = sub_match.group("key").strip().lower()
                val = sub_match.group("val").strip()

                if key in {"depends-on", "depends on", "dependencies"}:
                    deps = [
                        _strip_wrapping_backticks(part).strip()
                        for part in val.split(",")
                        if _strip_wrapping_backticks(part).strip()
                        and _strip_wrapping_backticks(part).strip().lower() != "none"
                    ]
                    for dep in deps:
                        if dep not in current.depends_on:
                            current.depends_on.append(dep)
                    continue
                if key == "scope":
                    current.scope = val
                    continue
                if key in {"constraint", "constraints"}:
                    current.constraints = val
                    continue
                if key in {"verification", "test", "tests"}:
                    current.verification = _strip_wrapping_backticks(val) or "pytest"
                    continue
                if key in {"git-commit", "commit"}:
                    current.commit_message = _strip_wrapping_backticks(val)
                    continue
                if key in {"acceptance-criteria", "acceptance criteria"}:
                    if val:
                        for part in val.split(";"):
                            cleaned = part.strip()
                            if cleaned:
                                current.acceptance_criteria.append(cleaned)
                    else:
                        in_ac_list = True
                    continue

            current.extra_lines.append(line.rstrip())
            continue

        # Unindented line outside a new task (trailing section/note) -> stop active task
        tasks.append(current)
        current = None
        in_ac_list = False

    if current is not None:
        tasks.append(current)

    preamble = "\n".join(preamble_lines).rstrip()
    return (preamble + "\n" if preamble else "", tasks)


def parse_manifest(content: str) -> list[TaskItem]:
    """Parse `TASKS.md` markdown content into a list of `TaskItem` entries."""
    _, tasks = parse_manifest_document(content)
    return tasks


def serialize_task(task: TaskItem) -> list[str]:
    """Render a single `TaskItem` into normalized `TASKS.md` markdown lines."""
    issue_suffix = f" (Issue #{task.issue_number})" if task.issue_number is not None else ""
    lines = [f"- {task.status.checkbox} **{task.task_id}: {task.title}**{issue_suffix}"]
    if task.depends_on:
        lines.append(f"  - **Depends-On:** {', '.join(task.depends_on)}")
    if task.scope:
        lines.append(f"  - **Scope:** {task.scope}")
    if task.constraints:
        lines.append(f"  - **Constraints:** {task.constraints}")
    if task.acceptance_criteria:
        lines.append("  - **Acceptance-Criteria:**")
        for ac in task.acceptance_criteria:
            lines.append(f"    - {ac}")
    if task.verification:
        lines.append(f"  - **Verification:** `{task.verification}`")
    if task.commit_message:
        lines.append(f"  - **Git-Commit:** `{task.commit_message}`")
    for extra in task.extra_lines:
        lines.append(extra)
    return lines


def serialize_manifest(
    tasks: Sequence[TaskItem],
    *,
    header: str | None = None,
) -> str:
    """Serialize `TaskItem` models back into lossless `TASKS.md` markdown."""
    doc_header = DEFAULT_MANIFEST_HEADER if header is None else header.rstrip("\n") + "\n"
    blocks = ["\n".join(serialize_task(task)) for task in tasks]
    if not blocks:
        return doc_header if doc_header.strip() else ""
    prefix = f"{doc_header}\n" if doc_header.strip() else ""
    return prefix + "\n\n".join(blocks) + "\n"


def merge_manifests(
    existing_tasks: Sequence[TaskItem],
    incoming_tasks: Sequence[TaskItem],
    *,
    suggest_order: bool = True,
) -> list[TaskItem]:
    """Incrementally merge newly fetched tasks into an existing `TASKS.md` list.

    Preserves completed (`[x]`) and blocked (`[BLOCKED]`) task states, dependencies,
    and manual developer notes (`extra_lines`).
    """
    merged_by_id: dict[str, TaskItem] = {}
    issue_to_id: dict[int, str] = {}
    ordered_keys: list[str] = []

    for existing in existing_tasks:
        copy_item = existing.model_copy(deep=True)
        if copy_item.task_id not in merged_by_id:
            ordered_keys.append(copy_item.task_id)
        merged_by_id[copy_item.task_id] = copy_item
        if copy_item.issue_number is not None:
            issue_to_id[copy_item.issue_number] = copy_item.task_id

    for incoming in incoming_tasks:
        target_id = incoming.task_id
        if target_id not in merged_by_id and incoming.issue_number in issue_to_id:
            target_id = issue_to_id[incoming.issue_number]

        if target_id not in merged_by_id:
            copy_in = incoming.model_copy(deep=True)
            merged_by_id[copy_in.task_id] = copy_in
            ordered_keys.append(copy_in.task_id)
            if copy_in.issue_number is not None:
                issue_to_id[copy_in.issue_number] = copy_in.task_id
            continue

        existing_item = merged_by_id[target_id]
        # Never overwrite DONE or BLOCKED tasks, their dependencies, or manual notes.
        if existing_item.status in {TaskStatus.DONE, TaskStatus.BLOCKED}:
            continue

        combined_deps = list(existing_item.depends_on)
        for dep in incoming.depends_on:
            if dep not in combined_deps:
                combined_deps.append(dep)

        merged_by_id[target_id] = TaskItem(
            task_id=existing_item.task_id,
            issue_number=incoming.issue_number or existing_item.issue_number,
            title=incoming.title or existing_item.title,
            status=existing_item.status,
            depends_on=combined_deps,
            scope=incoming.scope or existing_item.scope,
            constraints=incoming.constraints or existing_item.constraints,
            acceptance_criteria=(incoming.acceptance_criteria or existing_item.acceptance_criteria),
            verification=incoming.verification or existing_item.verification,
            commit_message=incoming.commit_message or existing_item.commit_message,
            extra_lines=list(existing_item.extra_lines),
        )

    combined = [merged_by_id[tid] for tid in ordered_keys]
    return DependencyResolver().resolve(combined, suggest_order=suggest_order)
