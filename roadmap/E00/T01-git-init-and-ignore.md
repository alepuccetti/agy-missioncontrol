# Task E00-T01: Git Repository Initialization, MIT License, Branch Protection Baseline & `.gitignore`

- **Task ID:** `E00-T01`
- **Parent Epic:** [Epic E00: Workspace Permissions, Command Guards, Python Tooling & Repository Setup](./EPIC.md)
- **Status:** `[x] DONE`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `infra-specialist` (or Primary Orchestrator)
- **Dependencies:** None

---

## 1. User Story & Strategic Value

> **As a** Lead Developer and Open-Source Maintainer,  
> **I want** to initialize the local Git repository on default branch `main` with an open-source MIT License and a comprehensive, hardened `.gitignore`,  
> **So that** transient Python artifacts, virtual environments, local test caches, temporary agent logs, and local SQLite databases are never accidentally staged or committed to public source control, and the repository is properly licensed for community use and enhanced public CI compute allowances.

---

## 2. Testable Acceptance Criteria

- [x] **AC-1:** Git repository is initialized with default branch set to `main`.
- [x] **AC-2:** Open-source `LICENSE` file is created at repository root containing the standard MIT License text.
- [x] **AC-3:** Root `.gitignore` ignores all Python cache files (`__pycache__/`, `*.pyc`), virtual environments (`.venv/`, `env/`), build distributions (`dist/`, `build/`, `*.egg-info`), testing outputs (`.pytest_cache/`, `.coverage`, `htmlcov/`), IDE folders (`.vscode/`, `.idea/`), local SQLite state databases (`*.db`, `*.db-wal`, `*.db-shm`, `*.db-journal`), and `agymissioncontrol` runtime dumps (`.agymissioncontrol/`).
- [x] **AC-4:** `git status` executes cleanly without tracking ignored patterns.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `LICENSE` | Create | Open-source MIT License text |
| `.gitignore` | Create | Comprehensive ignore rules for Python, tools, caches, and runtime artifacts |

---

## 4. Technical Implementation Specification

### 4.1 Git Initialization
Execute `git init -b main` in the project root directory.

### 4.2 Open-Source MIT License (`LICENSE`)
Standard MIT License granting full public permissions:
```text
MIT License

Copyright (c) 2026 agymissioncontrol Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
...
```

### 4.3 `.gitignore` Contents
Must include:
```gitignore
# Byte-compiled / optimized files
__pycache__/
*.py[cod]
*$py.class

# Distribution / packaging
dist/
build/
*.egg-info/

# Virtual environments
.venv/
venv/
env/

# Testing & Coverage
.pytest_cache/
.coverage
htmlcov/
coverage.xml

# Linters & Tooling
.ruff_cache/
.mypy_cache/

# Embedded SQLite Databases & WAL logs
*.db
*.db-wal
*.db-shm
*.db-journal

# Runtime Application Artifacts
.agymissioncontrol/
*.log

# OS & IDE metadata
.DS_Store
.idea/
.vscode/
*.swp
```

---

## 5. Verification & Definition of Done

- [x] Execute `git status` and verify repository is on branch `main`.
- [x] Create a dummy file in `.venv/test.txt`, `.agymissioncontrol/missioncontrol.db`, and `.agymissioncontrol/test.log` and verify they are omitted by `git status --porcelain`.
- [x] Remove dummy files after verification.
