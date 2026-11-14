# Task E00-T05: GitHub CLI Integration, Remote Origin Setup & Standard Issue Label Taxonomy

- **Task ID:** `E00-T05`
- **Parent Epic:** [Epic E00: Workspace Permissions, Command Guards, Python Tooling & Repository Setup](./EPIC.md)
- **Status:** `[x] DONE`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `infra-specialist` (or Primary Orchestrator)
- **Dependencies:** `E00-T01`

---

## 1. User Story & Strategic Value

> **As an** Orchestrator,  
> **I want** GitHub CLI authentication verified, the remote repository origin linked, and a standardized label taxonomy established,  
> **So that** `agymissioncontrol plan` and `agymissioncontrol run` can seamlessly query remote issues, sync milestones, and close completed tickets without manual browser operations.

---

## 2. Testable Acceptance Criteria

- [x] **AC-1:** `gh auth status` verifies that GitHub CLI is authenticated with `repo` scopes.
- [x] **AC-2:** Public open-source GitHub repository `origin` is confirmed or created using `gh repo create agymissioncontrol --public --source=. --remote=origin` / `git remote add`.
- [x] **AC-3:** Standard label taxonomy script `scripts/setup_labels.sh` is created to provision labels:
  - `agent-ready`: Issues validated for autonomous agent execution.
  - `epic`: Milestone parent issues.
  - `blocked`: Tasks requiring human intervention or prerequisite tasks.
  - `area/quota`, `area/manifest`, `area/runner`, `area/cli`.
- [x] **AC-4:** Script executes idempotently via `gh label create --force`.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `scripts/setup_labels.sh` | Create | Script provisioning standard GitHub label taxonomy |

---

## 4. Technical Implementation Specification

### 4.1 Label Taxonomy Script (`scripts/setup_labels.sh`)
```bash
#!/usr/bin/env bash
set -euo pipefail

labels=(
  "agent-ready:0E8A16:Ready for autonomous agent execution"
  "epic:3E4B9E:Top-level architectural epic"
  "blocked:D93F0B:Blocked by upstream dependencies or quota"
  "area/quota:1D76DB:Quota governor, sliding window and rate limiting"
  "area/manifest:5319E7:Task manifest parser, serializer, and TASKS.md"
  "area/runner:0052CC:Execution supervisor, verification, and detached runner"
  "area/cli:BFD4F2:CLI framework, configuration, and terminal UX"
)

for item in "${labels[@]}"; do
  IFS=":" read -r name color desc <<< "$item"
  echo "Syncing label: $name"
  gh label create "$name" --color "$color" --description "$desc" --force || true
done
```

---

## 5. Verification & Definition of Done

- [x] Execute `gh auth status` and confirm valid token.
- [x] Execute `bash scripts/setup_labels.sh` and verify all labels exist on GitHub.
- [x] Verify `git remote -v` outputs valid remote origin.
