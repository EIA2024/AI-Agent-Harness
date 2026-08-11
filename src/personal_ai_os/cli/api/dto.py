"""API data-transfer objects for the Personal AI OS REST API (CLI v2, T10).

Plain frozen dataclasses constructed from server JSON via ``from_dict``.
Constructors are tolerant of missing fields so the client survives additive
server changes (forward compatibility).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _d(data: dict[str, Any], key: str, default: Any = None) -> Any:
    return data.get(key, default)


@dataclass(frozen=True)
class SessionDTO:
    id: str
    title: str = ""
    status: str = "active"
    channel: str = "api"
    project_id: str | None = None
    active_run_id: str | None = None
    agent_id: str | None = None
    created_at: str | None = None
    last_active_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionDTO:
        return cls(
            id=str(_d(data, "id")),
            title=_d(data, "title") or "",
            status=_d(data, "status") or "active",
            channel=_d(data, "channel") or "api",
            project_id=_d(data, "project_id"),
            active_run_id=_d(data, "active_run_id"),
            agent_id=_d(data, "agent_id"),
            created_at=_d(data, "created_at"),
            last_active_at=_d(data, "last_active_at"),
        )


@dataclass(frozen=True)
class ToolCallDTO:
    id: str
    tool_name: str = ""
    status: str = ""
    risk_level: int = 0
    arguments: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    approval_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCallDTO:
        return cls(
            id=str(_d(data, "id")),
            tool_name=_d(data, "tool_name") or "",
            status=_d(data, "status") or "",
            risk_level=_d(data, "risk_level") or 0,
            arguments=_d(data, "arguments") or {},
            result=_d(data, "result"),
            error=_d(data, "error"),
            approval_id=_d(data, "approval_id"),
            started_at=_d(data, "started_at"),
            completed_at=_d(data, "completed_at"),
        )


@dataclass(frozen=True)
class RunDTO:
    id: str
    status: str = "running"
    session_id: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    model_usage: dict[str, Any] | None = None
    cost: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[ToolCallDTO] = field(default_factory=list)

    @property
    def final_response(self) -> str:
        return str((self.state or {}).get("final_response") or "")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunDTO:
        return cls(
            id=str(_d(data, "id")),
            status=_d(data, "status") or "running",
            session_id=_d(data, "session_id"),
            input=_d(data, "input") or {},
            state=_d(data, "state") or {},
            model_usage=_d(data, "model_usage"),
            cost=_d(data, "cost"),
            error=_d(data, "error"),
            started_at=_d(data, "started_at"),
            completed_at=_d(data, "completed_at"),
            created_at=_d(data, "created_at"),
            steps=_d(data, "steps") or [],
            tool_calls=[
                ToolCallDTO.from_dict(tc)
                for tc in (_d(data, "tool_calls") or [])
                if isinstance(tc, dict)
            ],
        )


@dataclass(frozen=True)
class ApprovalDTO:
    id: str
    run_id: str | None = None
    session_id: str | None = None
    tool_name: str = ""
    action_summary: str = ""
    arguments_preview: dict[str, Any] | None = None
    risk_level: int = 0
    risk_reason: str = ""
    argument_hash: str | None = None
    status: str = "pending"
    approved_by: str | None = None
    created_at: str | None = None
    expires_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalDTO:
        return cls(
            id=str(_d(data, "id")),
            run_id=_d(data, "run_id"),
            session_id=_d(data, "session_id"),
            tool_name=_d(data, "tool_name") or "",
            action_summary=_d(data, "action_summary") or "",
            arguments_preview=_d(data, "arguments_preview"),
            risk_level=_d(data, "risk_level") or 0,
            risk_reason=_d(data, "risk_reason") or "",
            argument_hash=_d(data, "argument_hash"),
            status=_d(data, "status") or "pending",
            approved_by=_d(data, "approved_by"),
            created_at=_d(data, "created_at"),
            expires_at=_d(data, "expires_at"),
        )


@dataclass(frozen=True)
class MemoryDTO:
    id: str
    content: str = ""
    summary: str | None = None
    type: str = "fact"
    scope: str = "global"
    importance: float = 0.5
    confidence: float = 0.5
    source_type: str = "manual"
    sensitivity: str = "personal"
    status: str = "active"
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryDTO:
        return cls(
            id=str(_d(data, "id")),
            content=_d(data, "content") or "",
            summary=_d(data, "summary"),
            type=_d(data, "type") or "fact",
            scope=_d(data, "scope") or "global",
            importance=_d(data, "importance") or 0.5,
            confidence=_d(data, "confidence") or 0.5,
            source_type=_d(data, "source_type") or "manual",
            sensitivity=_d(data, "sensitivity") or "personal",
            status=_d(data, "status") or "active",
            created_at=_d(data, "created_at"),
            updated_at=_d(data, "updated_at"),
        )


@dataclass(frozen=True)
class ToolDTO:
    name: str
    namespace: str = ""
    description: str = ""
    source: str = ""
    risk_level: int = 0
    side_effect: bool = False
    destructive: bool = False
    external_write: bool = False
    idempotent: bool = False
    tags: list[str] = field(default_factory=list)
    version: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolDTO:
        return cls(
            name=_d(data, "name") or "",
            namespace=_d(data, "namespace") or "",
            description=_d(data, "description") or "",
            source=_d(data, "source") or "",
            risk_level=_d(data, "risk_level") or 0,
            side_effect=bool(_d(data, "side_effect", False)),
            destructive=bool(_d(data, "destructive", False)),
            external_write=bool(_d(data, "external_write", False)),
            idempotent=bool(_d(data, "idempotent", False)),
            tags=list(_d(data, "tags") or []),
            version=_d(data, "version") or "",
        )


@dataclass(frozen=True)
class AuditDTO:
    id: str
    event_type: str = ""
    resource_type: str = ""
    resource_id: str | None = None
    actor_type: str = ""
    actor_id: str | None = None
    details: dict[str, Any] | None = None
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditDTO:
        return cls(
            id=str(_d(data, "id")),
            event_type=_d(data, "event_type") or "",
            resource_type=_d(data, "resource_type") or "",
            resource_id=_d(data, "resource_id"),
            actor_type=_d(data, "actor_type") or "",
            actor_id=_d(data, "actor_id"),
            details=_d(data, "details"),
            created_at=_d(data, "created_at"),
        )


@dataclass(frozen=True)
class MessageDTO:
    id: str
    role: str = ""
    content: str = ""
    session_id: str | None = None
    run_id: str | None = None
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageDTO:
        return cls(
            id=str(_d(data, "id")),
            role=_d(data, "role") or "",
            content=_d(data, "content") or "",
            session_id=_d(data, "session_id"),
            run_id=_d(data, "run_id"),
            created_at=_d(data, "created_at"),
        )


@dataclass(frozen=True)
class SendResult:
    """Response of ``POST /v1/sessions/{id}/messages``."""

    run_id: str | None
    status: str = "running"
    message_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SendResult:
        run_id = _d(data, "run_id")
        return cls(
            run_id=str(run_id) if run_id else None,
            status=_d(data, "status") or "running",
            message_id=_d(data, "message_id"),
        )


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Best-effort parse of a raw LLM tool-call ``arguments`` (JSON string or dict)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"raw": raw}
    return {}
