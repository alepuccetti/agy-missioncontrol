"""GitHub CLI (`gh`) issue tracker adapter."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

from agymissioncontrol.manifest.tracker.base import (
    IssueTracker,
    RawIssue,
    TrackerError,
    payload_to_raw_issue,
    run_cli_json,
)

GH_ISSUE_FIELDS = "number,title,body,labels,milestone"


class GitHubTracker(IssueTracker):
    """Fetch issues and dependency links from GitHub via `gh` CLI."""

    def __init__(
        self,
        *,
        repo: str | None = None,
        limit: int = 100,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._repo = repo
        self._limit = limit
        self._runner = runner

    def fetch_issues(
        self,
        *,
        milestone: str | None = None,
        labels: list[str] | None = None,
        issue_number: int | None = None,
        state: str = "open",
    ) -> list[RawIssue]:
        """Invoke `gh issue list` (or `view` for a single issue) and normalize payloads."""
        if issue_number is not None:
            cmd = ["gh", "issue", "view", str(issue_number), "--json", GH_ISSUE_FIELDS]
            if self._repo:
                cmd.extend(["--repo", self._repo])
            payload = run_cli_json(cmd, cli_name="gh", runner=self._runner)
            if not isinstance(payload, dict):
                raise TrackerError("Unexpected `gh issue view` JSON payload type.")
            return [payload_to_raw_issue(payload)]

        cmd = [
            "gh",
            "issue",
            "list",
            "--json",
            GH_ISSUE_FIELDS,
            "--state",
            state,
            "--limit",
            str(self._limit),
        ]
        if self._repo:
            cmd.extend(["--repo", self._repo])
        if milestone:
            cmd.extend(["--milestone", milestone])
        if labels:
            for label in labels:
                cmd.extend(["--label", label])

        payload = run_cli_json(cmd, cli_name="gh", runner=self._runner)
        if not isinstance(payload, list):
            raise TrackerError("Unexpected `gh issue list` JSON payload type.")

        issues: list[RawIssue] = []
        for item in payload:
            if not isinstance(item, dict):
                raise TrackerError("Unexpected issue entry inside `gh issue list` output.")
            issues.append(payload_to_raw_issue(item))
        return issues

    @staticmethod
    def parse_issue_payload(payload: dict[str, Any]) -> RawIssue:
        """Convert a single raw GitHub JSON issue payload to `RawIssue`."""
        return payload_to_raw_issue(payload)
