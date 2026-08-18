"""AppState for the CLI presentation domain (CLI v2, T13).

A pure, serializable snapshot that the reducer maintains and that both the
headless renderers and the TUI render from. No network, no filesystem.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class ConnectionState(enum.StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


class APIConnectionState(enum.StrEnum):
    UNKNOWN = "unknown"
    CHECKING = "checking"
    CONNECTED = "connected"
    ERROR = "error"


class ProviderStatusSource(Protocol):
    """Public fields returned by ``AsyncAPIClient.get_provider_status``."""

    mode: str
    profile: str | None
    provider: str
    model: str
    reasoning_effort: str
    capabilities: dict[str, Any]


@dataclass
class ProviderViewState:
    """Secret-free snapshot of the API service's active provider."""

    mode: str = "unknown"
    profile: str | None = None
    provider: str = "unknown"
    model: str = "unknown"
    reasoning_effort: str = "auto"
    model_enumeration: bool = False
    reasoning_efforts: tuple[str, ...] = ("auto",)

    @property
    def is_echo(self) -> bool:
        return self.mode == "echo"

    @classmethod
    def from_api_status(cls, status: ProviderStatusSource) -> ProviderViewState:
        capabilities = status.capabilities or {}
        efforts = capabilities.get("reasoning_efforts")
        if not isinstance(efforts, (list, tuple)):
            efforts = ("auto",)
        public_efforts = tuple(
            effort
            for effort in (str(value) for value in efforts)
            if effort in {"auto", "low", "medium", "high"}
        ) or ("auto",)
        return cls(
            mode=str(status.mode or "unknown"),
            profile=str(status.profile) if status.profile else None,
            provider=str(status.provider or "unknown"),
            model=str(status.model or "unknown"),
            reasoning_effort=str(status.reasoning_effort or "auto"),
            model_enumeration=bool(capabilities.get("model_enumeration", False)),
            reasoning_efforts=public_efforts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "profile": self.profile,
            "provider": self.provider,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "model_enumeration": self.model_enumeration,
            "reasoning_efforts": list(self.reasoning_efforts),
        }


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
    api_connection_state: APIConnectionState = APIConnectionState.UNKNOWN
    provider_status: ProviderViewState = field(default_factory=ProviderViewState)
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

    @property
    def model(self) -> str | None:
        model = self.provider_status.model
        return None if model == "unknown" else model

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "run_status": self.run_status,
            "connection_state": self.connection_state.value,
            "api_connection_state": self.api_connection_state.value,
            "provider_status": self.provider_status.to_dict(),
            "transcript": [c.__dict__ for c in self.transcript],
            "tool_calls": {k: v.to_dict() for k, v in self.tool_calls.items()},
            "pending_approval": self.pending_approval,
            "final_response": self.final_response,
            "last_error": self.last_error,
        }


def initial_state(*, session_id: str | None = None) -> AppState:
    return AppState(session_id=session_id)


def begin_provider_status_query(state: AppState) -> None:
    state.api_connection_state = APIConnectionState.CHECKING


def apply_provider_status(state: AppState, status: ProviderStatusSource) -> None:
    """Install the exact secret-free status returned by the API service."""
    state.provider_status = ProviderViewState.from_api_status(status)
    state.api_connection_state = APIConnectionState.CONNECTED


def fail_provider_status_query(state: AppState) -> None:
    state.api_connection_state = APIConnectionState.ERROR
