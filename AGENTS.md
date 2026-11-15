# Repository Agent Instructions

Follow [AGENT_ARCHITECTURE_PLAN.md](./AGENT_ARCHITECTURE_PLAN.md) for architecture, execution signals, and verification rules.

## GitHub Epics & Issue Taxonomy

- **Always track Epics using the GitHub Issue Type `Epic`**, never with an `epic` label:
  - Creating an Epic: `gh issue create --type Epic --title "[E0X] ..." ...`
  - Updating an existing Epic: `gh issue edit <issue-number> --type Epic`
  - Do **not** pass `--label epic` or `--add-label epic`, and do **not** recreate an `epic` label.
- Keep Epic and Task issue bodies synchronized with local `roadmap/` updates (`gh issue edit <number> --body-file ...`) before closing issues.
- Use `scripts/setup_labels.sh` to provision standard labels (`agent-ready`, `blocked`, `area/*`).

## Quality Gate

- Run `task check` (`ruff format --check`, `ruff check`, `pytest tests/ -v`) before committing.
