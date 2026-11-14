# Task E00-T03: Antigravity Pre-Tool Hooks (`.agents/hooks.json`), `plugin.json` & `command_guard.py`

- **Task ID:** `E00-T03`
- **Parent Epic:** [Epic E00: Workspace Permissions, Command Guards, Python Tooling & Repository Setup](./EPIC.md)
- **Status:** `[x] DONE`
- **Execution Mode:** 🧠 Deep Verify
- **Primary Agent:** `infra-specialist` (or Primary Orchestrator)
- **Dependencies:** `E00-T02`

---

## 1. User Story & Strategic Value

> **As an** Orchestrator and Security Guardian,  
> **I want** an Antigravity pre-tool command guard script and hook configuration adhering strictly to Antigravity's lifecycle hook schema for macOS and Linux (including headless Remote Agent cloud VMs),  
> **So that** local development, testing, `task` runner invocations, and Git inspections run with zero friction, while potentially destructive commands (e.g. `rm -rf /`, `git push --force`, out-of-workspace writes) are strictly caught and gated.

---

## 2. Testable Acceptance Criteria

- [x] **AC-1:** `.agents/hooks.json` and `plugin/hooks.json` configure `PreToolUse` hooks matching Antigravity's schema (`matcher: "run_command"`), intercepting execution via `command_guard.py`.
- [x] **AC-2:** `plugin/plugin.json` is created at the plugin root with name `agymissioncontrol`, allowing Antigravity to discover and activate the plugin when symlinked or installed.
- [x] **AC-3:** `.agents/scripts/command_guard.py` parses standard Antigravity stdin JSON (`{"toolCall": {"name": "run_command", "args": {"CommandLine": "..."}}, ...}`) with fallback to flat `{"CommandLine": "..."}` for test ergonomics.
- [x] **AC-4:** Evaluates commands into Antigravity decision outcomes emitted as JSON on stdout:
  - `{"decision": "allow"}`: local git read/write, `task *` invocations, `uv`, `pytest`, `ruff`, `agy`, python execution.
  - `{"decision": "ask", "reason": "..."}`: external pushes (`git push`), operations requiring operator confirmation.
  - `{"decision": "deny", "reason": "..."}`: destructive root removals, attempts to write outside workspace, or credential probing.
- [x] **AC-5:** Remote Agent Compatibility: `command_guard.py` runs non-interactively on macOS and Linux (glibc cloud VMs), gracefully handling EOF/empty input without hanging.
- [x] **AC-6:** Unit tests in `tests/test_command_guard.py` verify that safe `task` commands emit `"allow"`, dangerous commands emit `"deny"`, pushes emit `"ask"`, and headless stdin payloads are parsed cleanly.

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `plugin/plugin.json` | Create | Plugin manifest declaring `agymissioncontrol` for Antigravity discovery |
| `.agents/hooks.json` | Create | Workspace Antigravity pre-tool hook registration (`PreToolUse`) |
| `plugin/hooks.json` | Create | Distributable plugin hook registration utilizing relative script path |
| `.agents/scripts/command_guard.py` | Create | Workspace command validation, regex pattern matching, and JSON decision generator |
| `plugin/scripts/command_guard.py` | Create | Distributable plugin command guard script invoked by `plugin/hooks.json` |
| `tests/test_command_guard.py` | Create | Unit test suite for command guard rules and hook contract compliance |

---

## 4. Technical Implementation Specification

### 4.1 `plugin/plugin.json` Manifest
```json
{
  "name": "agymissioncontrol",
  "description": "Autonomous task orchestrator, quota governor, and issue-to-spec compiler for Google Antigravity"
}
```

### 4.2 `.agents/hooks.json` & `plugin/hooks.json`
Workspace-level hook (`.agents/hooks.json`):
```json
{
  "command-guard": {
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [
          {
            "type": "command",
            "command": "python3 .agents/scripts/command_guard.py"
          }
        ]
      }
    ]
  }
}
```

Plugin-level hook (`plugin/hooks.json`):
```json
{
  "command-guard": {
    "PreToolUse": [
      {
        "matcher": "run_command",
        "hooks": [
          {
            "type": "command",
            "command": "python3 scripts/command_guard.py"
          }
        ]
      }
    ]
  }
}
```
*Note on Plugin Execution:* Antigravity executes plugin hooks with the plugin directory as CWD, allowing scripts to be referenced cleanly via relative path `scripts/command_guard.py`.

### 4.3 `command_guard.py` Contract & Auto-Approval Rules
- **Input Parsing:**
  ```python
  import sys, json, re

  raw = sys.stdin.read().strip()
  if not raw:
      print(json.dumps({"decision": "allow"}))
      sys.exit(0)

  data = json.loads(raw)
  # Extract CommandLine from Antigravity nested toolCall or fallback flat key
  cmd = data.get("toolCall", {}).get("args", {}).get("CommandLine") or data.get("CommandLine", "")
  ```
- **Rules:**
  - `allow`: `task (check*|test*|lint*|format*|dev*|install*|ci*|clean*|build*)`, `git (status|diff|add|commit|branch|checkout|log|restore|clean)`, `uv (run|sync|pip|venv|tool)`, `pytest`, `ruff`, `agy (run|status|gate|-p|--print)`
  - `ask`: `git push`, deletions outside workspace
  - `deny`: `rm -rf /`, `/etc`, `~/.ssh`
- **Output:**
  - Prints `json.dumps({"decision": decision, "reason": reason})` to stdout and exits `0`.

---

## 5. Verification & Definition of Done

- [x] Run `python3 .agents/scripts/command_guard.py` with payload `{"toolCall": {"name": "run_command", "args": {"CommandLine": "task check"}}}` and verify stdout contains `{"decision": "allow"}`.
- [x] Run with dangerous payload `{"CommandLine": "rm -rf /"}` and verify stdout contains `{"decision": "deny"}`.
- [x] Run `uv run pytest tests/test_command_guard.py` with 100% pass rate.
