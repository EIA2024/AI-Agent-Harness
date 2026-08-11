"""AppState for the CLI presentation domain (CLI v2, T13).

A pure, serializable snapshot that the reducer maintains and that both the
headless renderers and the TUI render from. No network, no filesystem.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ConnectionState(enum.StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


@dataclass
class ToolViewState:
    """UI-facing state of one tool call, keyed by stable tool_call_id."""

    id: str
    name: str = ""
    status: str = "requested"  # requested | running | completed | failed
    risk_level: int = 0
    arguments_preview: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    result_preview: str = ""
    error: str = ""
    latency_ms: int | None = None
    started_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "risk_level": self.risk_level,
            "arguments_preview": self.arguments_preview,
            "summary": self.summary,
            "result_preview": self.result_preview,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "started_at": self.started_at,
        }


# Transcript cell kinds (the typed cells the plan specifies).
CELL_USER = "user"
CELL_ASSISTANT = "assistant"
CELL_PROGRESS = "progress"
CELL_TOOL = "tool"
CELL_APPROVAL = "approval"
CELL_RUN_STATUS = "run_status"
CELL_ERROR = "error"
CELL_NOTICE = "notice"
CELL_WARNING = "warning"


@dataclass
class TranscriptCell:
    kind: str
    ts: datetime | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    text: str = ""


@dataclass
class AppState:
    session_id: str | None = None
    run_id: str | None = None
    run_status: str = "unknown"  # running | waiting_approval | completed | failed | cancelled
    connection_state: ConnectionState = ConnectionState.DISCONNECTED
    model: str | None = None
    transcript: list[TranscriptCell] = field(default_factory=list)
    tool_calls: dict[str, ToolViewState] = field(default_factory=dict)
    pending_approval: dict[str, Any] | None = None
    assistant_buffer: str = ""
    final_response: str = ""
    last_error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def last_cell(self) -> TranscriptCell | None:
        return self.transcript[-1] if self.transcript else None

    @property
    def assistant_text(self) -> str:
        """Full accumulated assistant text from all assistant cells."""
        parts = [c.text for c in self.transcript if c.kind == CELL_ASSISTANT]
        return "\n".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "connection_state": self.connection_state.value,
            "model": self.model,
            "transcript": [c.__dict__ for c in self.transcript],
            "tool_calls": {k: v.to_dict() for k, v in self.tool_calls.items()},
            "pending_approval": self.pending_approval,
            "final_response": self.final_response,
            "last_error": self.last_error,
        }


def initial_state(*, session_id: str | None = None) -> AppState:
    return AppState(session_id=session_id)
