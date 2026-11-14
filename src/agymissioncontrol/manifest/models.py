"""Domain models for compiled `TASKS.md` execution manifests."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class TaskStatus(StrEnum):
    """Execution status checkbox marker inside `TASKS.md`."""

    TODO = " "
    DONE = "x"
    BLOCKED = "BLOCKED"

    @classmethod
    def from_marker(cls, raw: str) -> TaskStatus:
        """Parse bracket marker content or named status strings into `TaskStatus`."""
        cleaned = raw.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned[1:-1].strip()
        upper = cleaned.upper()
        if cleaned == "" or upper in {"TODO", "OPEN", "PENDING"}:
            return cls.TODO
        if upper in {"X", "DONE", "COMPLETE", "COMPLETED"}:
            return cls.DONE
        if upper in {"BLOCKED", "!"}:
            return cls.BLOCKED
        raise ValueError(f"Unsupported task status marker: {raw!r}")

    @property
    def checkbox(self) -> str:
        """Return markdown checkbox token (e.g. `[ ]`, `[x]`, `[BLOCKED]`)."""
        return f"[{self.value}]"


class TaskItem(BaseModel):
    """Structured representation of a single task entry inside `TASKS.md`."""

    task_id: str
    issue_number: int | None = None
    title: str
    status: TaskStatus = TaskStatus.TODO
    depends_on: list[str] = Field(default_factory=list)
    scope: str = ""
    constraints: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    verification: str = "pytest"
    commit_message: str = ""
    extra_lines: list[str] = Field(default_factory=list)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, value: TaskStatus | str) -> TaskStatus:
        if isinstance(value, TaskStatus):
            return value
        return TaskStatus.from_marker(str(value))
