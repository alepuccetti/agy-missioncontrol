"""Unit tests for DoD extraction and verification command synthesis (`E02-T03`)."""

from __future__ import annotations

from agymissioncontrol.manifest.extractor import extract_task_spec
from agymissioncontrol.manifest.models import TaskStatus


def test_extracts_explicit_verification_keyword_and_commit_scope_from_labels() -> None:
    body = """
## Summary
Implement token bucket middleware in `src/agymissioncontrol/api/rate_limit.py`.

## Acceptance Criteria
- [ ] Enforce per-client sliding bucket quotas
- [ ] Return HTTP 429 on exhaustion
- [ ] #99

- **Constraints:** Must avoid external Redis dependency at startup.
- **Verification:** `pytest tests/test_rate_limit.py && ruff check .`
"""
    task = extract_task_spec(
        body,
        "Add In-Memory Rate Limiting Middleware",
        101,
        default_test="pytest",
        labels=["area/api", "enhancement"],
        blocked_by_numbers=[99],
    )
    assert task.task_id == "TASK-101"
    assert task.issue_number == 101
    assert task.status == TaskStatus.TODO
    assert task.depends_on == ["TASK-099"]
    assert task.verification == "pytest tests/test_rate_limit.py && ruff check ."
    assert task.constraints == "Must avoid external Redis dependency at startup."
    assert task.acceptance_criteria == [
        "Enforce per-client sliding bucket quotas",
        "Return HTTP 429 on exhaustion",
    ]
    assert task.commit_message == "feat(api): add In-Memory Rate Limiting Middleware (closes #101)"


def test_extracts_fenced_bash_command_under_testing_section() -> None:
    body = """
## Scope
Parse config overrides cleanly.

## Testing
Run the focused suite before opening a PR:

```bash
uv run pytest tests/test_config.py -v
```
"""
    task = extract_task_spec(body, "Config overrides", 12, default_test="pytest")
    assert task.verification == "uv run pytest tests/test_config.py -v"
    assert task.scope == "Parse config overrides cleanly."


def test_synthesizes_pytest_from_mentioned_test_file() -> None:
    body = """
Implement sliding window helpers in `src/agymissioncontrol/quota/sliding_window.py`
and add unit coverage in `tests/test_sliding_window.py`.
"""
    task = extract_task_spec(body, "Sliding window helpers", 13, default_test="cargo test")
    assert task.verification == "pytest tests/test_sliding_window.py"
    assert task.commit_message == "feat(quota): sliding window helpers (closes #13)"


def test_falls_back_to_default_test_when_no_commands_or_test_files_exist() -> None:
    body = "Refactor internal documentation and improve user-facing copy."
    task = extract_task_spec(body, "Polish CLI copy", 44, default_test="cargo test")
    assert task.verification == "cargo test"
    assert task.commit_message == "feat(core): polish CLI copy (closes #44)"
