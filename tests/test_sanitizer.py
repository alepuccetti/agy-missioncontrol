"""Unit tests for the issue body noise sanitization pipeline (`E02-T02`)."""

from __future__ import annotations

from agymissioncontrol.manifest.sanitizer import TRUNCATION_MARKER, sanitize_issue_body


def test_strips_html_comments_and_badges() -> None:
    raw = """
<!-- Please fill out all sections carefully -->
[![CI](https://github.com/org/repo/actions/workflows/ci.yml/badge.svg)](https://github.com/org/repo/actions)
![Coverage](https://img.shields.io/badge/coverage-95%25-brightgreen)

## Summary
Implement sliding window quota persistence in `src/agymissioncontrol/quota/sliding_window.py`.

<!-- hidden internal note
multi-line comment -->
"""
    cleaned = sanitize_issue_body(raw)
    assert "<!--" not in cleaned
    assert "shields.io" not in cleaned
    assert "badge.svg" not in cleaned
    assert "## Summary" in cleaned
    assert "src/agymissioncontrol/quota/sliding_window.py" in cleaned


def test_strips_empty_headers_and_boilerplate_sections() -> None:
    raw = """
## Details
### Environment
_No response_

### Steps to reproduce
N/A

### Screenshots
<!-- attach screenshot if helpful -->

### Pre-submission checklist
- [x] I searched existing issues
- [ ] I have read the contributing guide

## Acceptance Criteria
- [ ] Export JSON output via `--json` flag
- [ ] Preserve `src/agymissioncontrol/cli.py` compatibility
"""
    cleaned = sanitize_issue_body(raw)
    assert "### Environment" not in cleaned
    assert "### Steps to reproduce" not in cleaned
    assert "### Screenshots" not in cleaned
    assert "### Pre-submission checklist" not in cleaned
    assert "## Details" not in cleaned
    assert "## Acceptance Criteria" in cleaned
    assert "- [ ] Export JSON output via `--json` flag" in cleaned
    assert "src/agymissioncontrol/cli.py" in cleaned


def test_truncates_oversized_log_blocks_while_preserving_short_code_snippets() -> None:
    long_log = "\n".join(f"2026-11-14 INFO line {idx:02d}" for idx in range(1, 61))
    short_code = "\n".join(
        [
            "def validate_config(path: str) -> bool:",
            "    <!-- do not strip comments inside code snippets -->",
            "    return path.endswith('.yaml')",
        ]
    )
    raw = f"""
## Problem
Crash dump below:

```text
{long_log}
```

## Example Fix

```python
{short_code}
```
"""
    cleaned = sanitize_issue_body(raw)
    assert TRUNCATION_MARKER in cleaned
    assert "line 01" in cleaned
    assert "line 05" in cleaned
    assert "line 06" not in cleaned
    assert "line 30" not in cleaned
    assert "line 55" not in cleaned
    assert "line 56" in cleaned
    assert "line 60" in cleaned
    assert "<!-- do not strip comments inside code snippets -->" in cleaned
    assert "def validate_config(path: str) -> bool:" in cleaned


def test_preserves_meaningful_reproduction_steps_and_empty_input() -> None:
    assert sanitize_issue_body("") == ""
    assert sanitize_issue_body("   \n  ") == ""

    raw = """
### Environment
macOS 15.1, Python 3.15

### Steps to reproduce
1. Run `agymissioncontrol plan --provider mock`
2. Inspect generated `TASKS.md`
"""
    cleaned = sanitize_issue_body(raw)
    assert "### Environment" in cleaned
    assert "macOS 15.1, Python 3.15" in cleaned
    assert "### Steps to reproduce" in cleaned
    assert "agymissioncontrol plan --provider mock" in cleaned
