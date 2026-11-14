"""GitLab CLI (`glab`) issue tracker adapter."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

from agymissioncontrol.manifest.tracker.base import (
    IssueTracker,
    RawIssue,
    TrackerError,
    filter_raw_issues,
    payload_to_raw_issue,
    run_cli_json,
)


class GitLabTracker(IssueTracker):
    """Fetch issues and dependency links from GitLab via `glab` CLI."""

    def __init__(
        self,
        *,
        repo: str | None = None,
        per_page: int = 100,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._repo = repo
        self._per_page = per_page
        self._runner = runner

    def fetch_issues(
        self,
        *,
        milestone: str | None = None,
        labels: list[str] | None = None,
        issue_number: int | None = None,
        state: str = "open",
    ) -> list[RawIssue]:
        """Invoke `glab issue list` (or `view`) and normalize JSON payloads."""
        if issue_number is not None:
            cmd = ["glab", "issue", "view", str(issue_number), "--output", "json"]
            if self._repo:
                cmd.extend(["--repo", self._repo])
            payload = run_cli_json(cmd, cli_name="glab", runner=self._runner)
            if not isinstance(payload, dict):
                raise TrackerError("Unexpected `glab issue view` JSON payload type.")
            return [self.parse_issue_payload(payload)]

        cmd = ["glab", "issue", "list", "--output", "json", "--per-page", str(self._per_page)]
        if state == "closed":
            cmd.append("--closed")
        elif state == "all":
            cmd.append("--all")
        if self._repo:
            cmd.extend(["--repo", self._repo])
        if milestone:
            cmd.extend(["--milestone", milestone])
        if labels:
            cmd.extend(["--label", ",".join(labels)])

        payload = run_cli_json(cmd, cli_name="glab", runner=self._runner)
        if not isinstance(payload, list):
            raise TrackerError("Unexpected `glab issue list` JSON payload type.")

        issues = [self.parse_issue_payload(item) for item in payload if isinstance(item, dict)]
        if len(issues) != len(payload):
            raise TrackerError("Unexpected issue entry inside `glab issue list` output.")
        return filter_raw_issues(issues, milestone=milestone, labels=labels)

    @staticmethod
    def parse_issue_payload(payload: dict[str, Any]) -> RawIssue:
        """Convert a single raw GitLab JSON issue payload to `RawIssue`."""
        return payload_to_raw_issue(
            payload,
            number_keys=("iid", "number", "id"),
            body_keys=("description", "body"),
        )
