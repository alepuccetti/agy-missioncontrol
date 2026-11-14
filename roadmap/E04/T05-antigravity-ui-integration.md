# Task E04-T05: Antigravity IDE UI Status Pill & Webview Auxiliary Pane Integration

- **Task ID:** `E04-T05`
- **Parent Epic:** [Epic E04: Multi-Agent Process Registry & Live Web Cockpit](./EPIC.md)
- **Status:** `[ ] TODO`
- **Execution Mode:** ⚡ Direct
- **Primary Agent:** `frontend-developer`
- **Dependencies:** `E04-T04`

---

## 1. User Story & Strategic Value

> **As an** Antigravity IDE User,  
> **I want** the `agymissioncontrol` plugin to render a live status indicator pill and an inline Webview dashboard directly inside the Antigravity Auxiliary Pane,  
> **So that** I don't even need to leave the IDE to see how many agents are sleeping and when they will wake up.

---

## 2. Testable Acceptance Criteria

- [ ] **AC-1:** The Antigravity plugin `plugin/skills/agymissioncontrol/SKILL.md` defines an IDE status widget and dashboard command.
- [ ] **AC-2:** Surfaces a dynamic UI pill in chat:
  - If 0 agents sleeping: `[💤 Mission Control: All Active](http://localhost:8484)`
  - If $N$ agents sleeping: `[💤 2 Agents Sleeping (Next wake in 24m)](http://localhost:8484)`
- [ ] **AC-3:** Clicking the pill opens the local dashboard in an Antigravity auxiliary webview panel or launches the local browser.
- [ ] **AC-4 (Ergonomic Slash Commands - QW-3):** Implements slash command `/missioncontrol-status` alongside short aliases `/mc` and `/mc-status` inside the Antigravity chat that print an inline interactive table with live sleep times and a manual refresh link.
- [ ] **AC-5:** Manual and automated testing confirms the IDE renders the pill cleanly across themes (light and dark).

---

## 3. Exact File Manifest

| Path | Action | Description |
| :--- | :---: | :--- |
| `plugin/skills/agymissioncontrol/SKILL.md` | Create | Add `/missioncontrol-status` slash command and auxiliary panel definition |
| `plugin/skills/agymissioncontrol/scripts/ui_widget.py` | Create | Generates inline status pills and IDE markdown widgets |
| `tests/test_antigravity_plugin_ui.py` | Create | Unit tests validating IDE widget output and URL actions |

---

## 4. Technical Implementation Specification

### 4.1 Antigravity Status Widget Helper
```python
from datetime import datetime, timezone
from agymissioncontrol.registry.manager import list_active_sessions

def generate_ide_status_pill() -> str:
    sessions = list_active_sessions()
    sleeping = [s for s in sessions if s.state == "SLEEPING"]
    if not sleeping:
        return "[💤 Mission Control: All Active](http://localhost:8484)"
    
    earliest_wake = min(s.wakes_at for s in sleeping if s.wakes_at)
    minutes_left = max(0, int((earliest_wake - datetime.now(timezone.utc)).total_seconds() / 60))
    return f"[💤 {len(sleeping)} Agents Sleeping ({minutes_left}m left)](http://localhost:8484)"
```

---

## 5. Verification & Definition of Done

- [ ] Unit test verifies `generate_ide_status_pill()` computes correct sleeping count and minutes remaining.
- [ ] Verify `/missioncontrol-status` command renders markdown table and dashboard link.
- [ ] `task check` executes cleanly with 100% test pass rate.
