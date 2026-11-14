# Task E01-T01: Strongly Typed Configuration Models (`.agymissioncontrol.yaml` with Session Caps & Circuit Breakers) & Discovery Engine

- **Task ID:** `E01-T01`
- **Parent Epic:** [Epic E01: Quota Governor, Dual-Tier Quota & Session Circuit Breakers (`gate` / `status`)](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `core-developer`
- **Dependencies:** `E00-T02`

---

## 1. User Story & Strategic Value

> **As an** Automation Operator running across multiple repositories,  
> **I want** a configuration engine with project-scoped path resolution (`--project-dir`) and dynamic runtime directory resolution (`<project_root>/.agymissioncontrol/`),  
> **So that** running `agymissioncontrol` across different projects never collides with other repositories' logs, reports, or task manifests.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** `src/agymissioncontrol/config.py` models configuration sections: `project`, `models`, `governor`, `execution`.
- [ ] **AC-2:** Dynamic Project Scoping: `load_config(path, project_dir)` resolves `project_root` from explicit `--project-dir` or CWD, and anchors `runtime_dir = project_root / ".agymissioncontrol"`.
- [ ] **AC-3:** Zero Collision Invariant: All outputs (`TASKS.md`, logs, reports) are strictly anchored inside `project_root`.
- [ ] **AC-4:** Automatic `.gitignore` Guard: Verifies `<project_root>/.gitignore` includes `.agymissioncontrol/`; automatically appends it if missing to prevent committing transient logs and reports.
- [ ] **AC-5 (Dual-Tier Quota & Credit Governance Model):**
  - Quota thresholds: `threshold_percent` (5h rolling), `weekly_threshold_percent` (weekly ceiling), and `max_session_tokens`.
  - AI Credit Configuration: `CreditConfig` with:
    - `policy`: `never` (default), `budget_capped`, or `always`.
    - `max_burn_usd`: Float ceiling (default: `$0.00` under `never`, user-defined under `budget_capped`).
    - `trigger`: `weekly_only` (default) vs `any`. By default, Mission Control only taps credits when weekly allowance is exhausted (5-hour window limits trigger a zero-token wait). Setting `any` permits bypassing the 5-hour window wait using credits.
    - `on_cap_reached`: `sleep` (default) or `halt`.
- [ ] **AC-6 (Calling Agent Overrides & Precedence Hierarchy):**
  - Calling agents can dynamically override `credit_policy`, `max_credit_burn`, and `credit_trigger` (or `--bypass-5h-quota`) via CLI flags or environment variables (`AGY_CREDIT_POLICY`, `AGY_MAX_CREDIT_BURN`, `AGY_CREDIT_TRIGGER`).
  - Precedence order: CLI Arguments > Environment Variables > Project YAML > Safe Defaults.
- [ ] **AC-7:** Unit tests in `tests/test_config.py` verify project-scoped resolution, AI credit model parsing, and precedence cascade.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `src/agymissioncontrol/config.py` | Create/Modify | Configuration models with dynamic `project_root`, AI credit governance, and override resolution |
| `tests/test_config.py` | Create/Modify | Unit tests validating project-scoped isolation, credit policies, and CLI/env override hierarchy |

---

## 4. Technical Implementation Specification

### 4.1 AI Credit Configuration Models
```python
from enum import Enum
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field

class CreditPolicy(str, Enum):
    NEVER = "never"
    BUDGET_CAPPED = "budget_capped"
    ALWAYS = "always"

class CreditTrigger(str, Enum):
    WEEKLY_ONLY = "weekly_only"  # Default: only burn credits when weekly allowance is depleted
    ANY = "any"                  # Bypass 5h rolling wait and tap credits on any quota exhaustion

class CreditConfig(BaseModel):
    policy: CreditPolicy = CreditPolicy.NEVER
    max_burn_usd: float = 0.00
    trigger: CreditTrigger = CreditTrigger.WEEKLY_ONLY
    on_cap_reached: str = "sleep"

class GovernorConfig(BaseModel):
    threshold_percent: float = 15.0
    weekly_threshold_percent: float = 10.0
    max_session_tokens: Optional[int] = None
    credits: CreditConfig = Field(default_factory=CreditConfig)
```

### 4.2 Project-Scoped Path Resolution
```python
class ResolvedPaths(BaseModel):
    project_root: Path
    runtime_dir: Path      # <project_root>/.agymissioncontrol
    manifest_path: Path    # <project_root>/TASKS.md
    logs_dir: Path         # <project_root>/.agymissioncontrol/logs
    reports_dir: Path      # <project_root>/.agymissioncontrol/reports

def resolve_project_paths(project_dir: Optional[Path] = None, manifest_override: Optional[str] = None) -> ResolvedPaths:
    root = (project_dir or Path.cwd()).resolve()
    runtime = root / ".agymissioncontrol"
    manifest = root / (manifest_override or "TASKS.md")
    return ResolvedPaths(
        project_root=root,
        runtime_dir=runtime,
        manifest_path=manifest,
        logs_dir=runtime / "logs",
        reports_dir=runtime / "reports",
    )
```

---

## 5. Verification & Definition of Done

- [ ] Unit test verifies running with `--project-dir /tmp/project-a` anchors all logs and reports in `/tmp/project-a/.agymissioncontrol/`.
- [ ] Unit test verifies running with `--project-dir /tmp/project-b` creates separate isolated artifacts without collision.
- [ ] `uv run pytest tests/test_config.py` passes 100%.
