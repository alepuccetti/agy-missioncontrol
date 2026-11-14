"""Offline mock issue tracker reading JSON/YAML fixture files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from agymissioncontrol.manifest.tracker.base import (
    IssueTracker,
    RawIssue,
    TrackerError,
    filter_raw_issues,
    payload_to_raw_issue,
)

DEFAULT_MOCK_ISSUES: list[dict[str, Any]] = [
    {
        "number": 101,
        "title": "Bootstrap core configuration loader",
        "body": (
            "## Acceptance Criteria\n"
            "- Load `missioncontrol.yaml` cleanly\n\n"
            "## Verification\n"
            "`pytest tests/test_config.py`\n"
        ),
        "labels": ["core", "priority:high"],
        "milestone": "v0.2.0",
        "parent_issue_number": 100,
    },
    {
        "number": 102,
        "title": "Implement sliding window ledger",
        "body": (
            "Blocked by #101\n\n"
            "## Acceptance Criteria\n"
            "- Track 5h rolling token quota in SQLite WAL\n\n"
            "## Verification\n"
            "`pytest tests/test_sliding_window.py`\n"
        ),
        "labels": ["core", "quota"],
        "milestone": "v0.2.0",
        "parent_issue_number": 100,
    },
    {
        "number": 103,
        "title": "Wire mission status CLI display",
        "body": (
            "## Dependencies\n"
            "- [ ] #101\n"
            "- [ ] #102\n\n"
            "## Acceptance Criteria\n"
            "- Render Rich status table\n\n"
            "## Verification\n"
            "`pytest tests/test_cli_gate_status.py`\n"
        ),
        "labels": ["cli", "ui"],
        "milestone": "v0.2.0",
        "parent_issue_number": 100,
    },
]


class MockTracker(IssueTracker):
    """Offline tracker reading pre-linked issue fixtures from JSON, YAML, or defaults."""

    def __init__(
        self,
        fixture_path: Path | str | None = None,
        *,
        issues: list[RawIssue | dict[str, Any]] | None = None,
    ) -> None:
        self._fixture_path = Path(fixture_path) if fixture_path is not None else None
        self._explicit_issues = issues

    def fetch_issues(
        self,
        *,
        milestone: str | None = None,
        labels: list[str] | None = None,
        issue_number: int | None = None,
        state: str = "open",
    ) -> list[RawIssue]:
        """Load fixture issues and apply requested filters."""
        _ = state
        raw_items = self._load_issues()
        return filter_raw_issues(
            raw_items,
            milestone=milestone,
            labels=labels,
            issue_number=issue_number,
        )

    def _load_issues(self) -> list[RawIssue]:
        if self._explicit_issues is not None:
            return [
                item if isinstance(item, RawIssue) else payload_to_raw_issue(item)
                for item in self._explicit_issues
            ]

        if self._fixture_path is None:
            return [payload_to_raw_issue(entry) for entry in DEFAULT_MOCK_ISSUES]

        if not self._fixture_path.exists():
            raise TrackerError(f"Mock fixture file does not exist: {self._fixture_path}")

        content = self._fixture_path.read_text(encoding="utf-8")
        suffix = self._fixture_path.suffix.lower()
        try:
            if suffix == ".json":
                parsed = json.loads(content)
            else:
                parsed = yaml.safe_load(content)
        except (json.JSONDecodeError, yaml.YAMLError) as exc:
            raise TrackerError(f"Invalid mock fixture in {self._fixture_path}: {exc}") from exc

        if isinstance(parsed, dict) and isinstance(parsed.get("issues"), list):
            entries = parsed["issues"]
        elif isinstance(parsed, list):
            entries = parsed
        else:
            raise TrackerError(
                f"Mock fixture {self._fixture_path} must be a list or {{'issues': [...]}}."
            )

        result: list[RawIssue] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise TrackerError("Every issue entry in a mock fixture must be a mapping.")
            result.append(payload_to_raw_issue(entry))
        return result
