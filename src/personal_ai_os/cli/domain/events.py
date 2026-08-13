"""Normalized UI events (CLI v2, T12).

The UI consumes only :class:`UIEvent` — never raw SSE payloads. This decouples
live/replay differences and server schema changes from the reducer and the
presentation layers (headless + TUI).
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SCHEMA_VERSION = 1


class UIEventType(enum.StrEnum):
    CONNECTION_OPENED = "connection.opened"
    CONNECTION_CLOSED = "connection.closed"
    RUN_STARTED = "run.started"
    PROGRESS = "progress"
    ASSISTANT_DELTA = "assistant.delta"
    ASSISTANT_COMPLETED = "assistant.completed"
    TOOL_REQUESTED = "tool.requested"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    APPROVAL_REQUIRED = "approval.required"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UIEvent:
    """One event the reducer / renderers understand."""

    type: UIEventType
    run_id: str | None = None
    ts: datetime | None = None
    seq: int | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "v": SCHEMA_VERSION,
            "type": self.type.value,
            "run_id": self.run_id,
            "ts": self.ts.isoformat() if self.ts else None,
            "seq": self.seq,
            "payload": dict(self.payload),
        }


def new_run_id(value: Any, fallback: str | None = None) -> str | None:
    """Coerce a payload run id to str, or use a fallback / generated id."""
    if value:
        return str(value)
    if fallback:
        return fallback
    return None


def ui_event(
    type_: UIEventType,
    payload: Mapping[str, Any] | None = None,
    *,
    run_id: str | None = None,
    ts: datetime | None = None,
    seq: int | None = None,
) -> UIEvent:
    return UIEvent(
        type=type_,
        payload=dict(payload or {}),
        run_id=run_id,
        ts=ts,
        seq=seq,
    )
