"""Global SQLite gate process & quota registry package."""

from agymissioncontrol.registry.db import get_registry_db, get_state_dir, init_registry
from agymissioncontrol.registry.manager import (
    is_pid_alive,
    list_active_sessions,
    register_session,
    resolve_target_pids,
    stop_all_sessions,
    stop_session,
    stop_target,
    unregister_session,
    update_session_state,
    wake_session,
    wake_target,
)
from agymissioncontrol.registry.models import SessionState, SessionStatus

__all__ = [
    "SessionState",
    "SessionStatus",
    "get_registry_db",
    "get_state_dir",
    "init_registry",
    "is_pid_alive",
    "list_active_sessions",
    "register_session",
    "resolve_target_pids",
    "stop_all_sessions",
    "stop_session",
    "stop_target",
    "unregister_session",
    "update_session_state",
    "wake_session",
    "wake_target",
]
