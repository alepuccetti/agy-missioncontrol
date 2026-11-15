"""Pydantic domain models for the global gate process & quota registry."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field


class SessionStatus(StrEnum):
    """Lifecycle states for registered `agymissioncontrol gate` processes."""

    ACTIVE = "ACTIVE"
    SLEEPING = "SLEEPING"
    COMPLETED = "COMPLETED"
    STALE = "STALE"


class SessionState(BaseModel):
    """Persisted state record for a single `agymissioncontrol gate` session."""

    pid: int
    project_name: str
    project_dir: str
    label: str = ""
    model: str
    quota_group: str
    state: SessionStatus
    sleep_started_at: datetime | None = None
    wakes_at: datetime | None = None
    credit_burn_usd: float = 0.0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field
    @property
    def remaining_sleep_seconds(self) -> int | None:
        """Return remaining sleep seconds in UTC when sleeping, else ``None``."""
        if self.state != SessionStatus.SLEEPING or self.wakes_at is None:
            return None
        wakes_utc = (
            self.wakes_at.replace(tzinfo=UTC)
            if self.wakes_at.tzinfo is None
            else self.wakes_at.astimezone(UTC)
        )
        return max(0, int((wakes_utc - datetime.now(UTC)).total_seconds()))
