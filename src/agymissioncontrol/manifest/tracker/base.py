"""Base issue tracker interface, raw issue model, and dependency/hierarchy helpers."""

from __future__ import annotations

import json
import re
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel, Field

BLOCKED_BY_PATTERN = re.compile(
    r"(?:\*{1,2}|__)?(?:blocked\s+by|depends\s+on)(?:\*{1,2}|__)?\s*:?\s*#?(\d+)",
    re.IGNORECASE,
)
_BLOCKED_CLAUSE_PATTERN = re.compile(
    r"(?:\*{1,2}|__)?(?:blocked\s+by|depends\s+on)(?:\*{1,2}|__)?\s*:?\s*([^\n.;]+)",
    re.IGNORECASE,
)
TASKLIST_ISSUE_PATTERN = re.compile(r"[-*]\s*\[\s*\]\s*#(\d+)")
PARENT_ISSUE_PATTERN = re.compile(
    r"(?:\*{1,2}|__)?(?:parent(?:\s+epic|\s+issue)?|part\s+of)(?:\*{1,2}|__)?\s*:?\s*#(\d+)",
    re.IGNORECASE,
)


class TrackerError(RuntimeError):
    """Raised when an issue tracker CLI invocation or fixture load fails."""


class RawIssue(BaseModel):
    """Normalized raw issue fetched from an upstream tracker or fixture."""

    number: int
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    milestone: str | None = None
    parent_issue_number: int | None = None
    blocked_by_numbers: list[int] = Field(default_factory=list)


def extract_issue_dependencies(body: str, *, self_number: int | None = None) -> list[int]:
    """Extract blocking issue numbers from `Blocked by` clauses and markdown tasklists."""
    if not body:
        return []

    deps: set[int] = set()
    for match in BLOCKED_BY_PATTERN.finditer(body):
        deps.add(int(match.group(1)))

    for clause_match in _BLOCKED_CLAUSE_PATTERN.finditer(body):
        clause = clause_match.group(1)
        # Collect multi-issue lists in the same clause (e.g., "Blocked by #12, #13")
        # while avoiding prose numbers that do not start with `#` after the first token.
        first_token = True
        for ref_match in re.finditer(r"(#?\d+)\b", clause):
            token = ref_match.group(1)
            if first_token or token.startswith("#"):
                deps.add(int(token.lstrip("#")))
            first_token = False

    for match in TASKLIST_ISSUE_PATTERN.finditer(body):
        deps.add(int(match.group(1)))

    if self_number is not None:
        deps.discard(self_number)

    return sorted(deps)


def extract_parent_issue_number(
    body: str,
    payload: dict[str, Any] | None = None,
    *,
    self_number: int | None = None,
) -> int | None:
    """Extract parent issue or epic number from payload hierarchy fields or body text."""
    candidate: int | None = None

    if payload:
        direct = payload.get("parent_issue_number")
        if isinstance(direct, int):
            candidate = direct
        else:
            for key in ("parent", "parent_issue", "parentIssue", "epic"):
                val = payload.get(key)
                if isinstance(val, int):
                    candidate = val
                    break
                if isinstance(val, dict):
                    num = val.get("number") or val.get("iid")
                    if isinstance(num, int):
                        candidate = num
                        break
            if candidate is None:
                url = payload.get("parent_issue_url") or payload.get("parent_url")
                if isinstance(url, str):
                    url_match = re.search(r"/issues/(\d+)$", url.strip())
                    if url_match:
                        candidate = int(url_match.group(1))

    if candidate is None and body:
        match = PARENT_ISSUE_PATTERN.search(body)
        if match:
            candidate = int(match.group(1))

    if candidate is not None and self_number is not None and candidate == self_number:
        return None
    return candidate


def normalize_labels(raw_labels: Any) -> list[str]:
    """Normalize string or dict label entries into clean label names."""
    if not raw_labels or not isinstance(raw_labels, list):
        return []
    result: list[str] = []
    for item in raw_labels:
        if isinstance(item, str):
            name = item.strip()
            if name:
                result.append(name)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("title")
            if isinstance(name, str) and name.strip():
                result.append(name.strip())
    return result


def normalize_milestone(raw_milestone: Any) -> str | None:
    """Normalize string or dict milestone payloads into a title string."""
    if raw_milestone is None:
        return None
    if isinstance(raw_milestone, str):
        stripped = raw_milestone.strip()
        return stripped or None
    if isinstance(raw_milestone, dict):
        title = raw_milestone.get("title") or raw_milestone.get("name")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None


def extract_payload_blocked_numbers(payload: dict[str, Any]) -> list[int]:
    """Extract explicit blocker issue numbers from structured payload fields."""
    numbers: list[int] = []
    for key in ("blocked_by_numbers", "blocked_by", "blockedBy"):
        raw = payload.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, int):
                numbers.append(item)
            elif isinstance(item, dict):
                num = item.get("number") or item.get("iid")
                if isinstance(num, int):
                    numbers.append(num)
    return numbers


def merge_blocked_numbers(
    explicit: Iterable[int] | None,
    extracted: Iterable[int],
    *,
    self_number: int | None = None,
) -> list[int]:
    """Combine explicit blocker numbers and body-extracted blocker numbers."""
    merged: set[int] = set(extracted)
    if explicit:
        for item in explicit:
            if isinstance(item, int):
                merged.add(item)
    if self_number is not None:
        merged.discard(self_number)
    return sorted(merged)


def run_cli_json(
    cmd: list[str],
    *,
    cli_name: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    timeout: int = 30,
) -> Any:
    """Execute a tracker CLI command and parse its JSON stdout."""
    exec_fn = runner or subprocess.run
    try:
        proc = exec_fn(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise TrackerError(f"`{cli_name}` CLI is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise TrackerError(f"`{cli_name}` CLI timed out after {timeout}s.") from exc
    except OSError as exc:
        raise TrackerError(f"Failed to invoke `{cli_name}` CLI: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit code {proc.returncode}"
        raise TrackerError(f"`{cli_name}` CLI failed: {detail}")

    raw_output = (proc.stdout or "").strip()
    if not raw_output:
        return []
    try:
        return json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise TrackerError(f"`{cli_name}` CLI returned invalid JSON: {exc}") from exc


def payload_to_raw_issue(
    payload: dict[str, Any],
    *,
    number_keys: tuple[str, ...] = ("number", "iid"),
    body_keys: tuple[str, ...] = ("body", "description"),
) -> RawIssue:
    """Build a normalized `RawIssue` from a GitHub, GitLab, or fixture payload dictionary."""
    number: int | None = None
    for key in number_keys:
        raw_num = payload.get(key)
        if isinstance(raw_num, int):
            number = raw_num
            break
    if number is None:
        raise TrackerError("Issue payload is missing an integer issue number/`iid`.")

    raw_title = payload.get("title")
    title = raw_title if isinstance(raw_title, str) else str(raw_title or "")

    body = ""
    for key in body_keys:
        raw_body = payload.get(key)
        if isinstance(raw_body, str):
            body = raw_body
            break

    labels = normalize_labels(payload.get("labels"))
    milestone = normalize_milestone(payload.get("milestone"))
    parent_issue_number = extract_parent_issue_number(body, payload, self_number=number)
    extracted_deps = extract_issue_dependencies(body, self_number=number)
    explicit_deps = extract_payload_blocked_numbers(payload)
    blocked_by_numbers = merge_blocked_numbers(explicit_deps, extracted_deps, self_number=number)

    return RawIssue(
        number=number,
        title=title,
        body=body,
        labels=labels,
        milestone=milestone,
        parent_issue_number=parent_issue_number,
        blocked_by_numbers=blocked_by_numbers,
    )


def filter_raw_issues(
    issues: Iterable[RawIssue],
    *,
    milestone: str | None = None,
    labels: list[str] | None = None,
    issue_number: int | None = None,
) -> list[RawIssue]:
    """Filter raw issues by issue number, milestone, and labels."""
    requested_labels = {label.lower() for label in labels} if labels else set()
    filtered: list[RawIssue] = []
    for issue in issues:
        if issue_number is not None and issue.number != issue_number:
            continue
        if milestone is not None and (issue.milestone or "").lower() != milestone.lower():
            continue
        if requested_labels:
            issue_labels = {lbl.lower() for lbl in issue.labels}
            if not requested_labels.issubset(issue_labels):
                continue
        filtered.append(issue)
    return filtered


class IssueTracker(ABC):
    """Abstract base interface for issue tracker adapters."""

    @abstractmethod
    def fetch_issues(
        self,
        *,
        milestone: str | None = None,
        labels: list[str] | None = None,
        issue_number: int | None = None,
        state: str = "open",
    ) -> list[RawIssue]:
        """Fetch raw issues matching the requested filters."""
