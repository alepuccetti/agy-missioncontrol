# Task E00-T04: Developer Automation (`Taskfile.yml`) & Pinned LTS GitHub Actions CI Workflow

- **Task ID:** `E00-T04`
- **Parent Epic:** [Epic E00: Workspace Permissions, Command Guards, Python Tooling & Repository Setup](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `infra-specialist` (or Primary Orchestrator)
- **Dependencies:** `E00-T02`

---

## 1. User Story & Strategic Value

> **As a** Developer and Subagent Orchestrator,  
> **I want** a modern `Taskfile.yml` powered by `go-task` and a continuous integration workflow (`.github/workflows/ci.yml`) pinned to the latest LTS OS images and action versions,  
> **So that** all development workflows—dependency sync, linting, formatting, testing, CI checks, local editable linking (`task dev:link`), plugin symlinks, frontend builds (`task ui:build`), and cross-platform standalone binary compilation (`task build:binary`)—are driven by declarative, cross-platform tasks locally and verified automatically in GitHub Actions on pinned, predictable LTS environments without floating `-latest` aliases.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** Root `Taskfile.yml` is created using Taskfile schema version 3.
- [ ] **AC-2:** Defines core task targets:
  - `task install`: Installs virtual environment and dependencies via `uv sync --dev`.
  - `task test`: Runs pytest suite (`uv run pytest tests/ -v`).
  - `task lint`: Runs Ruff linter (`uv run ruff check src/ tests/`).
  - `task format`: Formats codebase (`uv run ruff format src/ tests/`).
  - `task check`: Runs format check, lint, and test sequentially.
  - `task dev:link`: Installs editable CLI via `uv tool install --editable . --force` and symlinks `plugin/` into `~/.gemini/config/plugins/agymissioncontrol`.
  - `task plugin:unlink`: Removes the Antigravity plugin symlink cleanly.
  - `task ui:install`: Installs frontend dependencies via `pnpm` in `src/agymissioncontrol/ui/frontend`.
  - `task ui:build`: Compiles TypeScript + Alpine.js dashboard into `src/agymissioncontrol/ui/static/`.
  - `task ui:dev`: Starts Vite dev server for local frontend hot-reloading.
  - `task build:binary`: Automatically runs `ui:build`, then compiles standalone single-file binary with PyInstaller into `dist/agymissioncontrol`, bundling static assets with cross-platform path separators.
  - `task ci`: Executes full CI pipeline (`check` + dry-run binary build).
  - `task clean`: Cleans build, dist, and cache artifacts.
- [ ] **AC-3:** Running `task --list` prints formatted task descriptions with clean alignment.
- [ ] **AC-4:** Running `task check` on a clean workspace executes without errors and exits code `0`.
- [ ] **AC-5:** Continuous Integration Workflow (`.github/workflows/ci.yml`) triggers on pull requests and pushes to `main`. It pins runners strictly to supported LTS/current OS versions (`ubuntu-24.04`, `macos-15`) and latest pinned actions (`actions/checkout@v7`, `astral-sh/setup-uv@v10` with `python-version: "3.15"`, `pnpm/action-setup@v6` with `version: 12`, `actions/setup-node@v7` with Node 26 LTS, `arduino/setup-task@v3`), rejecting unpinned floating `-latest` runner tags.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `Taskfile.yml` | Create | Declarative go-task configuration file covering CI, linking, UI, and testing |
| `.github/workflows/ci.yml` | Create | GitHub Actions CI matrix pipeline pinned to latest LTS OS and actions versions |

---

## 4. Technical Implementation Specification

### 4.1 `Taskfile.yml` Specification
```yaml
version: '3'

vars:
  PACKAGE_NAME: agymissioncontrol
  SRC_DIR: src/agymissioncontrol
  UI_DIR: src/agymissioncontrol/ui/frontend
  PLUGIN_DIR: "{{.USER_WORKING_DIR}}/plugin"
  ANTIGRAVITY_PLUGIN_DIR: "{{.HOME}}/.gemini/config/plugins/agymissioncontrol"

tasks:
  default:
    desc: List available tasks
    cmds:
      - task --list

  install:
    desc: Install virtual environment and development dependencies
    cmds:
      - uv sync --dev

  test:
    desc: Run pytest test suite
    cmds:
      - uv run pytest tests/ -v

  lint:
    desc: Run Ruff linter
    cmds:
      - uv run ruff check src/ tests/

  format:
    desc: Format codebase with Ruff
    cmds:
      - uv run ruff format src/ tests/

  format:check:
    desc: Check formatting without modifying files
    cmds:
      - uv run ruff format --check src/ tests/

  check:
    desc: Run all quality checks (format check, lint, and tests)
    cmds:
      - task: format:check
      - task: lint
      - task: test

  dev:link:
    desc: Install CLI into PATH (editable) and link plugin into Antigravity
    deps: [install]
    cmds:
      - uv tool install --editable . --force
      - mkdir -p "{{.HOME}}/.gemini/config/plugins"
      - ln -sfn "{{.PLUGIN_DIR}}" "{{.ANTIGRAVITY_PLUGIN_DIR}}"
      - echo "Plugin linked and agymissioncontrol installed in PATH with live reload!"

  plugin:unlink:
    desc: Remove Antigravity plugin symlink
    cmds:
      - rm -f "{{.ANTIGRAVITY_PLUGIN_DIR}}"
      - echo "Plugin symlink removed."

  ui:install:
    desc: Install frontend dependencies (pnpm)
    cmds:
      - '[ -f "{{.UI_DIR}}/package.json" ] && pnpm --prefix {{.UI_DIR}} install || echo "Frontend package.json not yet scaffolded; skipping pnpm install."'

  ui:build:
    desc: Compile TypeScript + Alpine.js dashboard into static assets
    cmds:
      - mkdir -p src/agymissioncontrol/ui/static
      - '[ -f "{{.UI_DIR}}/package.json" ] && pnpm --prefix {{.UI_DIR}} build || echo "Using static asset placeholder."'

  ui:dev:
    desc: Start Vite dev server for frontend development
    cmds:
      - pnpm --prefix {{.UI_DIR}} dev

  build:binary:
    desc: Compile standalone single-file binary with PyInstaller (bundles static UI)
    deps: [install, ui:build]
    cmds:
      - uv run pyinstaller --onefile --name agymissioncontrol --clean --add-data "src/agymissioncontrol/ui/static:agymissioncontrol/ui/static" src/agymissioncontrol/cli.py
      - echo "Standalone binary compiled at dist/agymissioncontrol"

  ci:
    desc: Run full CI validation pipeline
    cmds:
      - task: check
      - task: build:binary

  clean:
    desc: Clean build, dist, and cache artifacts
    cmds:
      - rm -rf dist/ build/ *.egg-info/ .pytest_cache/ .ruff_cache/ .coverage htmlcov/
      - find . -type d -name "__pycache__" -exec rm -rf {} +
```

### 4.2 GitHub Actions CI Workflow (`.github/workflows/ci.yml`)
```yaml
name: CI Pipeline

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  validate:
    name: Lint, Test & Validate (${{ matrix.os }})
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os:
          - ubuntu-24.04  # Pinned Ubuntu 24.04 LTS (Noble Numbat)
          - macos-15      # Pinned macOS 15 (Apple Silicon ARM64)
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v7

      - name: Set up uv Toolchain
        uses: astral-sh/setup-uv@v10
        with:
          enable-cache: true
          python-version: "3.15"

      - name: Install pnpm Package Manager
        uses: pnpm/action-setup@v6
        with:
          version: 12

      - name: Set up Node.js LTS
        uses: actions/setup-node@v7
        with:
          node-version: 26  # Node.js 26 Active LTS

      - name: Set up Go-Task Runner
        uses: arduino/setup-task@v3
        with:
          version: 3.x

      - name: Install Dependencies
        run: |
          task install
          task ui:install

      - name: Execute Quality Checks (Lint, Format & Tests)
        run: task check

      - name: Validate Binary Compilation
        run: task build:binary
```

---

## 5. Verification & Definition of Done

- [ ] Run `task --list` and confirm all tasks render with descriptions.
- [ ] Run `task check` and confirm exit code 0.
- [ ] Run `task dev:link` and confirm `which agymissioncontrol` resolves and `~/.gemini/config/plugins/agymissioncontrol` exists.
- [ ] Verify `.github/workflows/ci.yml` syntax parses cleanly with no floating `-latest` OS tags (`ubuntu-24.04`, `macos-15`) and all actions are pinned to latest stable major versions (`checkout@v7`, `setup-uv@v10`, `action-setup@v6`, `setup-node@v7`, `setup-task@v3`).
