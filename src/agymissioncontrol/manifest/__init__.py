"""Issue ingestion, sanitization, extraction, and dependency-aware `TASKS.md` planning."""

from agymissioncontrol.manifest.dependencies import CyclicDependencyError, DependencyResolver
from agymissioncontrol.manifest.extractor import (
    build_commit_message,
    extract_task_spec,
    format_task_id,
)
from agymissioncontrol.manifest.models import TaskItem, TaskStatus
from agymissioncontrol.manifest.parser import (
    merge_manifests,
    parse_manifest,
    parse_manifest_document,
    serialize_manifest,
    serialize_task,
)
from agymissioncontrol.manifest.sanitizer import sanitize_issue_body
from agymissioncontrol.manifest.tracker import (
    GitHubTracker,
    GitLabTracker,
    IssueTracker,
    MockTracker,
    RawIssue,
    TrackerError,
)

__all__ = [
    "CyclicDependencyError",
    "DependencyResolver",
    "GitHubTracker",
    "GitLabTracker",
    "IssueTracker",
    "MockTracker",
    "RawIssue",
    "TaskItem",
    "TaskStatus",
    "TrackerError",
    "build_commit_message",
    "extract_task_spec",
    "format_task_id",
    "merge_manifests",
    "parse_manifest",
    "parse_manifest_document",
    "sanitize_issue_body",
    "serialize_manifest",
    "serialize_task",
]
