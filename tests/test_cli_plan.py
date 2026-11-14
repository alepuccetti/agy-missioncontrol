"""Typer CLI integration tests for `agymissioncontrol plan` (`E02-T05`)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agymissioncontrol.cli import app

runner = CliRunner()
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def test_plan_help_exposes_all_required_options() -> None:
    result = runner.invoke(app, ["plan", "--help"])
    assert result.exit_code == 0
    clean = _strip_ansi(result.output)
    for flag in (
        "--issue",
        "--provider",
        "--milestone",
        "--label",
        "--default-test",
        "--output",
        "--suggest-order",
        "--dry-run",
    ):
        assert flag in clean


def test_plan_dry_run_displays_rich_tree_and_does_not_write_file(tmp_path: Path) -> None:
    target = tmp_path / "TASKS.md"
    result = runner.invoke(
        app,
        ["plan", "--provider", "mock", "--dry-run", "--output", str(target)],
    )
    assert result.exit_code == 0
    clean = _strip_ansi(result.output)
    assert "Task Execution Plan (3 tasks):" in clean
    assert "1. TASK-101:" in clean
    assert "(Depends on: TASK-101" in clean
    assert "--dry-run, not written to disk" in clean
    assert not target.exists()


def test_plan_writes_topological_manifest_and_merges_losslessly(tmp_path: Path) -> None:
    fixture = tmp_path / "issues.json"
    fixture.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "number": 202,
                        "title": "Build API endpoint",
                        "body": (
                            "<!-- comment -->\n"
                            "Blocked by #201\n\n"
                            "### Environment\n"
                            "N/A\n\n"
                            "## Acceptance Criteria\n"
                            "- [ ] Expose `/v1/status`\n"
                        ),
                        "labels": ["area/api"],
                        "milestone": "v0.2.0",
                    },
                    {
                        "number": 201,
                        "title": "Bootstrap schema",
                        "body": "Setup DB layer.",
                        "labels": ["area/db"],
                        "milestone": "v0.2.0",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    output_file = tmp_path / "TASKS.md"
    res1 = runner.invoke(
        app,
        [
            "plan",
            "--fixture",
            str(fixture),
            "--milestone",
            "v0.2.0",
            "--default-test",
            "uv run pytest -q",
            "--output",
            str(output_file),
            "--project-dir",
            str(tmp_path),
        ],
    )
    assert res1.exit_code == 0
    assert output_file.exists()

    written = output_file.read_text(encoding="utf-8")
    assert "<!--" not in written
    assert "### Environment" not in written
    assert written.index("TASK-201") < written.index("TASK-202")
    assert "- **Depends-On:** TASK-201" in written
    assert "- **Verification:** `uv run pytest -q`" in written

    # Simulate marking TASK-201 DONE, adding a note on TASK-202, and re-running plan
    edited = written.replace(
        "- [ ] **TASK-201: Bootstrap schema**",
        "- [x] **TASK-201: Bootstrap schema**",
    )
    edited += "  - **Note:** Hand-verified endpoint contract.\n"
    output_file.write_text(edited, encoding="utf-8")

    res2 = runner.invoke(
        app,
        [
            "plan",
            "--fixture",
            str(fixture),
            "--output",
            str(output_file),
            "--project-dir",
            str(tmp_path),
        ],
    )
    assert res2.exit_code == 0
    merged = output_file.read_text(encoding="utf-8")
    assert "- [x] **TASK-201: Bootstrap schema**" in merged
    assert "  - **Note:** Hand-verified endpoint contract." in merged


def test_plan_single_issue_label_filter_and_cycle_detection(tmp_path: Path) -> None:
    cyclic_fixture = tmp_path / "cyclic.json"
    cyclic_fixture.write_text(
        json.dumps(
            [
                {"number": 10, "title": "Task A", "body": "Blocked by #11", "labels": ["core"]},
                {"number": 11, "title": "Task B", "body": "Blocked by #10", "labels": ["core"]},
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "OUT.md"
    err_res = runner.invoke(
        app,
        ["plan", "--fixture", str(cyclic_fixture), "--output", str(out)],
    )
    assert err_res.exit_code == 1
    assert "Cycle detected" in _strip_ansi(err_res.output)
    assert not out.exists()

    # Single issue selection on default mock provider
    single_res = runner.invoke(
        app,
        ["plan", "--provider", "mock", "--issue", "101", "--label", "core", "--dry-run"],
    )
    assert single_res.exit_code == 0
    single_clean = _strip_ansi(single_res.output)
    assert "Task Execution Plan (1 tasks):" in single_clean
    assert "TASK-101" in single_clean


def test_plan_auto_falls_back_offline_and_explicit_provider_fails_cleanly(
    mocker: Any,
    tmp_path: Path,
) -> None:
    mocker.patch("subprocess.run", side_effect=FileNotFoundError("gh missing"))

    explicit_err = runner.invoke(
        app,
        ["plan", "--provider", "github", "--output", str(tmp_path / "TASKS.md")],
    )
    assert explicit_err.exit_code == 1
    assert "Error fetching issues" in _strip_ansi(explicit_err.output)

    auto_ok = runner.invoke(
        app,
        ["plan", "--provider", "auto", "--dry-run"],
    )
    assert auto_ok.exit_code == 0
    assert "Task Execution Plan (3 tasks):" in _strip_ansi(auto_ok.output)

    # Verify mocked gh JSON works seamlessly with `--provider github`
    gh_payload = [
        {
            "number": 501,
            "title": "Add telemetry export",
            "body": "Verification: `pytest tests/test_cli_plan.py`",
            "labels": [{"name": "cli"}],
            "milestone": {"title": "v1.0"},
        }
    ]
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["gh"],
            returncode=0,
            stdout=json.dumps(gh_payload),
            stderr="",
        ),
    )
    gh_res = runner.invoke(
        app,
        ["plan", "--provider", "github", "--dry-run"],
    )
    assert gh_res.exit_code == 0
    assert "TASK-501: Add telemetry export" in _strip_ansi(gh_res.output)
