# Task E00-T02: Modern Python Packaging (`pyproject.toml`), Dependency Pinning & `uv` Toolchain

- **Task ID:** `E00-T02`
- **Parent Epic:** [Epic E00: Workspace Permissions, Command Guards, Python Tooling & Repository Setup](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `infra-specialist` (or Primary Orchestrator)
- **Dependencies:** `E00-T01`

---

## 1. User Story & Strategic Value

> **As an** Application Engineer,  
> **I want** a modern, standardized `pyproject.toml` utilizing `hatchling` and the `uv` toolchain with support for both editable local development (`uv tool install -e .`) and standalone single-binary compilation (`pyinstaller`),  
> **So that** developers enjoy instant live-reload iteration during development and end users can run a zero-dependency standalone binary in production without needing Python or virtualenvs installed.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** `pyproject.toml` and `.python-version` (`3.15`) are created at the repository root configuring `build-system` with `hatchling`.
- [ ] **AC-2:** Python version is constrained to `>=3.15` (pinned to `3.15` in `.python-version`).
- [ ] **AC-3:** Core dependencies are declared: `typer>=0.29.0`, `pydantic>=2.14.0`, `pyyaml>=6.0.3`, `rich>=15.0.0`.
- [ ] **AC-4:** Development & build dependencies declared in `dev` group: `pytest>=9.2.0`, `pytest-mock>=3.17.0`, `ruff>=0.17.0`, `pyinstaller>=6.22.0`.
- [ ] **AC-5:** CLI entrypoint `agymissioncontrol = "agymissioncontrol.cli:app"` is registered.
- [ ] **AC-6:** Tool configs for `ruff` and `pytest` are embedded in `pyproject.toml`.
- [ ] **AC-7:** `uv tool install --editable . --force` installs `agymissioncontrol` into `~/.local/bin/` backed by an isolated virtualenv pointing directly to local `src/agymissioncontrol/` for instant live reloads.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `.python-version` | Create | Pins Python toolchain to `3.15` for `uv` and CI |
| `pyproject.toml` | Create | Hatchling build definition, dependencies, CLI scripts, and tool configs |
| `src/agymissioncontrol/__init__.py` | Create | Package initialization and version declaration (`__version__ = "0.1.0"`) |
| `src/agymissioncontrol/cli.py` | Create | Minimal Typer entrypoint exposing `--version` |

---

## 4. Technical Implementation Specification

### 4.1 `pyproject.toml` Specification
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "agymissioncontrol"
version = "0.1.0"
description = "Autonomous task orchestrator, quota governor, and issue-to-spec compiler for Google Antigravity"
readme = "README.md"
requires-python = ">=3.15"
license = "MIT"
authors = [{ name = "Orion" }]
dependencies = [
    "typer>=0.29.0",
    "pydantic>=2.14.0",
    "pyyaml>=6.0.3",
    "rich>=15.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=9.2.0",
    "pytest-mock>=3.17.0",
    "ruff>=0.17.0",
    "pyinstaller>=6.22.0",
]

[project.scripts]
agymissioncontrol = "agymissioncontrol.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["src/agymissioncontrol"]

[tool.ruff]
line-length = 100
target-version = "py315"

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "B"]
ignore = []

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

---

## 5. Verification & Definition of Done

- [ ] Run `uv venv` and `uv sync --dev`.
- [ ] Run `uv tool install --editable . --force` and verify `which agymissioncontrol` points to `~/.local/bin/agymissioncontrol`.
- [ ] Execute `agymissioncontrol --version` and confirm output `0.1.0`.
- [ ] Verify that editing `src/agymissioncontrol/__init__.py` updates `agymissioncontrol --version` output immediately without reinstalling.
