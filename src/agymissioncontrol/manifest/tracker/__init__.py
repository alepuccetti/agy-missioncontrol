"""Issue tracker client adapters for GitHub, GitLab, and offline fixtures."""

from agymissioncontrol.manifest.tracker.base import (
    BLOCKED_BY_PATTERN,
    PARENT_ISSUE_PATTERN,
    TASKLIST_ISSUE_PATTERN,
    IssueTracker,
    RawIssue,
    TrackerError,
    extract_issue_dependencies,
    extract_parent_issue_number,
)
from agymissioncontrol.manifest.tracker.github import GitHubTracker
from agymissioncontrol.manifest.tracker.gitlab import GitLabTracker
from agymissioncontrol.manifest.tracker.mock import MockTracker

__all__ = [
    "BLOCKED_BY_PATTERN",
    "GitHubTracker",
    "GitLabTracker",
    "IssueTracker",
    "MockTracker",
    "PARENT_ISSUE_PATTERN",
    "RawIssue",
    "TASKLIST_ISSUE_PATTERN",
    "TrackerError",
    "extract_issue_dependencies",
    "extract_parent_issue_number",
]
