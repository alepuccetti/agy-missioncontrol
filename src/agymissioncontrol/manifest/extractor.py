"""Acceptance criteria, verification command, and commit message extraction."""

from __future__ import annotations

import re

from agymissioncontrol.manifest.models import TaskItem

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$", re.MULTILINE)
_KEYWORD_VERIFICATION_PATTERNS = (
    re.compile(
        r"(?:^|\n)\s*[-*]?\s*(?:\*{1,2})?(?:verification(?:\s+command)?|test(?:\s+command)?)"
        r"\s*:?(?:\*{1,2})?\s*`([^`\n]+)`",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\n)\s*[-*]?\s*(?:\*{1,2})?(?:verification(?:\s+command)?|test(?:\s+command)?)"
        r"\s*:(?:\*{1,2})?\s*([^\n]+)",
        re.IGNORECASE,
    ),
)
_KEYWORD_SCOPE_PATTERN = re.compile(
    r"(?:^|\n)\s*[-*]?\s*(?:\*{1,2})?scope\s*:?(?:\*{1,2})?\s*([^\n]+)",
    re.IGNORECASE,
)
_KEYWORD_CONSTRAINTS_PATTERN = re.compile(
    r"(?:^|\n)\s*[-*]?\s*(?:\*{1,2})?constraints\s*:?(?:\*{1,2})?\s*([^\n]+)",
    re.IGNORECASE,
)
_FENCED_SHELL_PATTERN = re.compile(
    r"```(?:bash|sh|shell|zsh|console)?\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)
_INLINE_BACKTICK_PATTERN = re.compile(r"`([^`\n]+)`")
_TEST_RUNNER_COMMAND_PATTERN = re.compile(
    r"^(?:uv\s+run\s+(?:pytest|ruff)|pytest|cargo\s+test|npm\s+(?:run\s+)?test|"
    r"pnpm\s+test|yarn\s+test|go\s+test|task\s+(?:check|test)|python\s+-m\s+(?:pytest|unittest))\b"
)
_TEST_FILE_PATTERN = re.compile(r"\b((?:tests/[\w/.-]*)?test_[\w/.-]+\.py)\b")
_ISSUE_REF_TASKLIST_PATTERN = re.compile(r"^#\d+\b")
_METADATA_BULLET_PATTERN = re.compile(
    r"^(?:\*{1,2})?(?:scope|constraints?|verification(?:\s+command)?|"
    r"depends-on|git-commit|test(?:\s+command)?)\s*:?(?:\*{1,2})?",
    re.IGNORECASE,
)
_SOURCE_PATH_SCOPE_PATTERN = re.compile(
    r"\bsrc/(?:agymissioncontrol/)?([a-zA-Z0-9_-]+)(?:/([a-zA-Z0-9_-]+))?"
)
_SCOPED_LABEL_PATTERN = re.compile(r"^(?:area|scope|module|comp|topic)[/:]([a-zA-Z0-9_-]+)$")

_KNOWN_SCOPE_LABELS = {
    "cli",
    "quota",
    "manifest",
    "ui",
    "docs",
    "ci",
    "infra",
    "api",
    "auth",
    "security",
    "config",
    "runner",
}


def format_task_id(issue_num: int) -> str:
    """Format canonical `TASK-XXX` identifier from an issue number."""
    return f"TASK-{issue_num:03d}"


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown document into `(heading_lower, content)` pairs."""
    lines = text.splitlines()
    sections: list[tuple[str, str]] = []
    heading = ""
    buf: list[str] = []
    for line in lines:
        match = _HEADING_PATTERN.match(line)
        if match:
            sections.append((heading, "\n".join(buf).strip()))
            heading = match.group(2).strip().lower().rstrip(":")
            buf = []
        else:
            buf.append(line)
    sections.append((heading, "\n".join(buf).strip()))
    return sections


def _extract_command_from_fence(block_body: str) -> str:
    lines = [
        line.strip()
        for line in block_body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return " && ".join(lines)


def _extract_verification_command(
    sanitized_body: str,
    title: str,
    default_test: str,
) -> str:
    # 1. Explicit `Verification:` / `Test:` markers (preferring backticked commands)
    for pattern in _KEYWORD_VERIFICATION_PATTERNS:
        match = pattern.search(sanitized_body)
        if match:
            candidate = match.group(1).strip().strip("`")
            if candidate and not candidate.startswith("["):
                return candidate

    # 2. Shell fenced blocks or inline runner commands under Verification/Testing headings
    sections = _split_sections(sanitized_body)
    verification_headings = ("verification", "testing", "test plan", "definition of done", "dod")
    for heading, body in sections:
        if any(tok in heading for tok in verification_headings):
            fence_match = _FENCED_SHELL_PATTERN.search(body)
            if fence_match:
                cmd = _extract_command_from_fence(fence_match.group(1))
                if cmd:
                    return cmd
            for tick_match in _INLINE_BACKTICK_PATTERN.finditer(body):
                cmd = tick_match.group(1).strip()
                if _TEST_RUNNER_COMMAND_PATTERN.match(cmd):
                    return cmd

    # 3. Any inline backtick test runner command anywhere in the body
    for tick_match in _INLINE_BACKTICK_PATTERN.finditer(sanitized_body):
        cmd = tick_match.group(1).strip()
        if _TEST_RUNNER_COMMAND_PATTERN.match(cmd):
            return cmd

    # 4. Referenced test file paths -> synthesize pytest command
    seen_files: list[str] = []
    for search_text in (sanitized_body, title):
        for path_match in _TEST_FILE_PATTERN.finditer(search_text):
            rel = path_match.group(1)
            if rel not in seen_files:
                seen_files.append(rel)
    if seen_files:
        return f"pytest {' '.join(seen_files)}"

    # 5. Fallback to configured default command
    return default_test.strip() or "pytest"


def _extract_acceptance_criteria(sanitized_body: str) -> list[str]:
    criteria: list[str] = []
    seen: set[str] = set()

    sections = _split_sections(sanitized_body)
    ac_headings = ("acceptance criteria", "definition of done", "dod", "requirements")
    in_ac_sections = [
        body for heading, body in sections if any(tok in heading for tok in ac_headings)
    ]

    for block in in_ac_sections:
        for line in block.splitlines():
            stripped = line.strip()
            cb = re.match(r"^[-*]\s*\[[ xX]?\]\s+(.+)$", stripped)
            bullet = re.match(r"^(?:[-*]|\d+\.)\s+(.+)$", stripped)
            item = (cb.group(1) if cb else bullet.group(1) if bullet else "").strip()
            if (
                item
                and not _ISSUE_REF_TASKLIST_PATTERN.match(item)
                and not _METADATA_BULLET_PATTERN.match(item)
                and item not in seen
            ):
                seen.add(item)
                criteria.append(item)

    if not criteria:
        for line in sanitized_body.splitlines():
            cb = re.match(r"^\s*[-*]\s*\[[ xX]?\]\s+(.+)$", line)
            if cb:
                item = cb.group(1).strip()
                if item and not _ISSUE_REF_TASKLIST_PATTERN.match(item) and item not in seen:
                    seen.add(item)
                    criteria.append(item)

    return criteria


def _clean_single_line(text: str, max_len: int = 240) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 3].rstrip() + "..."


def _extract_scope_text(sanitized_body: str, title: str) -> str:
    match = _KEYWORD_SCOPE_PATTERN.search(sanitized_body)
    if match:
        return _clean_single_line(match.group(1))

    for heading, body in _split_sections(sanitized_body):
        if heading in {"scope", "summary", "overview", "description", ""}:
            for line in body.splitlines():
                stripped = line.strip()
                if (
                    stripped
                    and not stripped.startswith("```")
                    and not stripped.startswith(">")
                    and not re.match(r"^[-*]\s*\[", stripped)
                ):
                    cleaned = stripped.lstrip("-* ").strip()
                    if cleaned:
                        return _clean_single_line(cleaned)

    return _clean_single_line(title)


def _extract_constraints_text(sanitized_body: str) -> str:
    match = _KEYWORD_CONSTRAINTS_PATTERN.search(sanitized_body)
    if match:
        return _clean_single_line(match.group(1))

    for heading, body in _split_sections(sanitized_body):
        if "constraint" in heading or "invariant" in heading:
            items = [
                line.strip().lstrip("-* ").strip() for line in body.splitlines() if line.strip()
            ]
            if items:
                return _clean_single_line("; ".join(items))
    return ""


def _infer_commit_scope(
    sanitized_body: str,
    title: str,
    labels: list[str] | None,
) -> str:
    if labels:
        for label in labels:
            lowered = label.strip().lower()
            scoped = _SCOPED_LABEL_PATTERN.match(lowered)
            if scoped:
                return scoped.group(1)
        for label in labels:
            lowered = label.strip().lower()
            if lowered in _KNOWN_SCOPE_LABELS:
                return lowered

    for text in (sanitized_body, title):
        path_match = _SOURCE_PATH_SCOPE_PATTERN.search(text)
        if path_match:
            candidate = path_match.group(1).lower()
            if candidate not in {"__init__", "main"}:
                return candidate

    return "core"


def _format_commit_summary(title: str) -> str:
    summary = title.strip().rstrip(".")
    summary = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", summary).strip()
    if len(summary) >= 2 and summary[0].isupper() and not summary[:2].isupper():
        summary = summary[0].lower() + summary[1:]
    return summary or "implement task"


def build_commit_message(
    title: str,
    issue_num: int,
    *,
    sanitized_body: str = "",
    labels: list[str] | None = None,
) -> str:
    """Synthesize a conventional commit message with issue closure reference."""
    scope = _infer_commit_scope(sanitized_body, title, labels)
    summary = _format_commit_summary(title)
    return f"feat({scope}): {summary} (closes #{issue_num})"


def extract_task_spec(
    sanitized_body: str,
    title: str,
    issue_num: int,
    default_test: str = "pytest",
    *,
    labels: list[str] | None = None,
    blocked_by_numbers: list[int] | None = None,
) -> TaskItem:
    """Compile a sanitized issue description into a structured `TaskItem` specification."""
    verification = _extract_verification_command(sanitized_body, title, default_test)
    acceptance_criteria = _extract_acceptance_criteria(sanitized_body)
    scope = _extract_scope_text(sanitized_body, title)
    constraints = _extract_constraints_text(sanitized_body)
    commit_message = build_commit_message(
        title,
        issue_num,
        sanitized_body=sanitized_body,
        labels=labels,
    )

    depends_on: list[str] = []
    if blocked_by_numbers:
        seen_deps: set[int] = set()
        for dep_num in blocked_by_numbers:
            if dep_num != issue_num and dep_num not in seen_deps:
                seen_deps.add(dep_num)
                depends_on.append(format_task_id(dep_num))

    return TaskItem(
        task_id=format_task_id(issue_num),
        issue_number=issue_num,
        title=title.strip(),
        depends_on=depends_on,
        scope=scope,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        verification=verification,
        commit_message=commit_message,
    )
