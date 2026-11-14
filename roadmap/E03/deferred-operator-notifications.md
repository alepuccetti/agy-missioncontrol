# Future Architecture Definition: Operator Notifications (Deferred)

- **Status:** Deferred / Post-V1 Capability
- **Parent Epic:** [Epic E03: Autonomous Execution Runner, Detached Supervisor & Telemetry (`run`)](./EPIC.md)

---

## 1. Overview & Deferred Scope

> **Note:** Operator desktop alerts and webhooks are deferred to post-v1 enhancements to focus core execution strictly on quota governance, isolated agent execution, and SQLite telemetry. This document serves as the architectural interface definition when notification integration is scheduled.

---

## 2. Interface Definitions

### 2.1 Event Types & Payload Models
```python
from enum import Enum
from pydantic import BaseModel
from typing import Optional

class EventType(str, Enum):
    RUN_COMPLETED = "RUN_COMPLETED"
    SLEEP_ENTERED = "SLEEP_ENTERED"
    HARD_LOCKOUT = "HARD_LOCKOUT"
    TASK_BLOCKED = "TASK_BLOCKED"

class NotificationEvent(BaseModel):
    event_type: EventType
    project_name: str
    task_id: Optional[str] = None
    message: str
    timestamp: str
```

### 2.2 Dispatcher Stubs
- **Desktop Alerts:** Evaluates `sys.platform`:
  - On macOS (`darwin`): Calls `osascript` notification banner.
  - On Linux (`linux`): Calls `notify-send` if present.
- **Webhook Dispatcher:** HTTP POST with 5s timeout to user-specified `notifications.webhook_url` in `.agymissioncontrol.yaml`.

```

---

## 5. Verification & Definition of Done

- [ ] Unit test verifies `send_desktop_notification` builds valid `osascript` invocation.
- [ ] Mock test verifies webhook POST payload includes task count, cost, and event type.
- [ ] `uv run pytest tests/test_notifications.py` passes 100%.
