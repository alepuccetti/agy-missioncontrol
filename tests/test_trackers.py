"""Unit tests for issue tracker client adapters (`E02-T01`)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from agymissioncontrol.manifest.tracker import (
    GitHubTracker,
    GitLabTracker,
    MockTracker,
    TrackerError,
    extract_issue_dependencies,
)
from agymissioncontrol.manifest.tracker.base import run_cli_json


def test_extract_issue_dependencies_patterns_and_tasklists() -> None:
    body = """
    > **Parent Epic:** #3
    > **Blocked by:** #11, #12
    Depends on #42 and blocked by 7.

    ### Checklist
    - [ ] #101
    - [x] #105 Already resolved
    * [ ] #102
    Self reference #99 should not match unless blocked by #99.
    """
    deps = extract_issue_dependencies(body, self_number=99)
    assert deps == [7, 11, 12, 42, 101, 102]


def test_github_tracker_list_and_hierarchy_parsing(mocker: Any) -> None:
    payload = [
        {
            "number": 21,
            "title": "Add manifest CLI",
            "body": "Blocked by #42\n\n### Dependencies\n- [ ] #101\n",
            "labels": [{"name": "cli"}, {"name": "enhancement"}],
            "milestone": {"title": "v0.2.0-beta"},
            "parent": {"number": 3},
        },
        {
            "number": 22,
            "title": "Follow-up docs",
            "body": "> **Parent Epic:** #3\nDepends on #21",
            "labels": ["docs"],
            "milestone": "v0.2.0-beta",
            "parent_issue_url": "https://api.github.com/repos/org/repo/issues/3",
        },
    ]
    mock_run = mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["gh"],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    tracker = GitHubTracker(repo="alepuccetti/agy-missioncontrol")
    issues = tracker.fetch_issues(milestone="v0.2.0-beta", labels=["cli"])

    called_cmd = mock_run.call_args[0][0]
    assert called_cmd[:5] == [
        "gh",
        "issue",
        "list",
        "--json",
        "number,title,body,labels,milestone",
    ]
    assert "--milestone" in called_cmd and "v0.2.0-beta" in called_cmd
    assert "--label" in called_cmd and "cli" in called_cmd
    assert "--repo" in called_cmd and "alepuccetti/agy-missioncontrol" in called_cmd

    assert len(issues) == 2
    assert issues[0].blocked_by_numbers == [42, 101]
    assert issues[0].parent_issue_number == 3
    assert issues[0].labels == ["cli", "enhancement"]
    assert issues[0].milestone == "v0.2.0-beta"
    assert issues[1].parent_issue_number == 3
    assert issues[1].blocked_by_numbers == [21]


def test_github_tracker_single_issue_view(mocker: Any) -> None:
    payload = {
        "number": 42,
        "title": "Single ticket",
        "body": "- [ ] #101",
        "labels": [],
        "milestone": None,
    }
    mock_run = mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["gh"],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    tracker = GitHubTracker()
    issues = tracker.fetch_issues(issue_number=42)
    called_cmd = mock_run.call_args[0][0]
    assert called_cmd[:4] == ["gh", "issue", "view", "42"]
    assert len(issues) == 1
    assert issues[0].blocked_by_numbers == [101]


def test_github_tracker_errors_raise_tracker_error(mocker: Any) -> None:
    mocker.patch("subprocess.run", side_effect=FileNotFoundError("gh missing"))
    with pytest.raises(TrackerError, match="not installed"):
        GitHubTracker().fetch_issues()

    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["gh"], returncode=1, stdout="", stderr="auth error"
        ),
    )
    with pytest.raises(TrackerError, match="auth error"):
        GitHubTracker().fetch_issues()

    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["gh"], returncode=0, stdout="{bad json", stderr=""
        ),
    )
    with pytest.raises(TrackerError, match="invalid JSON"):
        GitHubTracker().fetch_issues()


def test_gitlab_tracker_parses_iid_description_and_links(mocker: Any) -> None:
    payload = [
        {
            "iid": 55,
            "title": "GitLab issue task",
            "description": "Blocked by #42\n- [ ] #101",
            "labels": ["backend"],
            "milestone": {"title": "Sprint 2"},
            "epic": {"iid": 9},
        }
    ]
    mock_run = mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["glab"],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    tracker = GitLabTracker()
    issues = tracker.fetch_issues(milestone="Sprint 2", labels=["backend"])
    called_cmd = mock_run.call_args[0][0]
    assert called_cmd[:5] == ["glab", "issue", "list", "--output", "json"]
    assert len(issues) == 1
    assert issues[0].number == 55
    assert issues[0].parent_issue_number == 9
    assert issues[0].blocked_by_numbers == [42, 101]
    assert issues[0].milestone == "Sprint 2"


def test_mock_tracker_reads_json_and_yaml_fixtures(tmp_path: Path) -> None:
    fixture_data = {
        "issues": [
            {
                "number": 10,
                "title": "Root setup",
                "body": "Parent Epic: #1",
                "labels": ["infra"],
                "milestone": "v0.2.0",
            },
            {
                "number": 11,
                "title": "Service layer",
                "body": "Blocked by #10\n- [ ] #12",
                "labels": ["api"],
                "milestone": "v0.2.0",
                "parent_issue_number": 1,
            },
        ]
    }

    json_file = tmp_path / "issues.json"
    json_file.write_text(json.dumps(fixture_data), encoding="utf-8")
    yaml_file = tmp_path / "issues.yaml"
    yaml_file.write_text(yaml.safe_dump(fixture_data), encoding="utf-8")

    json_issues = MockTracker(json_file).fetch_issues(milestone="v0.2.0")
    yaml_issues = MockTracker(yaml_file).fetch_issues(labels=["api"])

    assert [issue.number for issue in json_issues] == [10, 11]
    assert json_issues[0].parent_issue_number == 1
    assert json_issues[1].blocked_by_numbers == [10, 12]
    assert len(yaml_issues) == 1
    assert yaml_issues[0].number == 11


def test_mock_tracker_default_fixtures_available_offline() -> None:
    issues = MockTracker().fetch_issues()
    assert len(issues) == 3
    by_num = {issue.number: issue for issue in issues}
    assert by_num[102].blocked_by_numbers == [101]
    assert by_num[103].blocked_by_numbers == [101, 102]


def test_run_cli_json_handles_timeout_and_oserror() -> None:
    def _timeout_runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["gh"], timeout=30)

    def _oserror_runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("permission denied")

    with pytest.raises(TrackerError, match="timed out"):
        run_cli_json(["gh", "issue", "list"], cli_name="gh", runner=_timeout_runner)

    with pytest.raises(TrackerError, match="Failed to invoke `glab`"):
        run_cli_json(["glab", "issue", "list"], cli_name="glab", runner=_oserror_runner)


def test_mock_tracker_rejects_missing_and_invalid_fixtures(tmp_path: Path) -> None:
    with pytest.raises(TrackerError, match="does not exist"):
        MockTracker(tmp_path / "missing.json").fetch_issues()

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(TrackerError, match="Invalid mock fixture"):
        MockTracker(bad_json).fetch_issues()

    bad_shape = tmp_path / "bad_shape.yaml"
    bad_shape.write_text('"just-a-string"\n', encoding="utf-8")
    with pytest.raises(TrackerError, match="must be a list"):
        MockTracker(bad_shape).fetch_issues()
