"""JSON serializers for the ORM models (safe, owner-aware API responses)."""

from __future__ import annotations

from personal_ai_os.common.utils import LogSanitizer
from personal_ai_os.db.models import (
    Approval,
    AuditEvent,
    Automation,
    MemoryRow,
    Message,
    Run,
    RunStep,
    Session,
    ToolCall,
    User,
)


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _u(value) -> str | None:
    return str(value) if value is not None else None


def user_to_dict(u: User) -> dict:
    return {
        "id": _u(u.id),
        "username": u.username,
        "display_name": u.display_name,
    }


def session_to_dict(
    s: Session,
    *,
    messages: list[Message] | None = None,
    message_count: int | None = None,
    active_run_status: str | None = None,
) -> dict:
    data = {
        "id": _u(s.id),
        "owner_id": _u(s.owner_id),
        "agent_id": _u(s.agent_id),
        "channel": s.channel,
        "external_conversation_id": s.external_conversation_id,
        "project_id": _u(s.project_id),
        "title": s.title,
        "status": s.status,
        "active_run_id": _u(s.active_run_id),
        "created_at": _iso(s.created_at),
        "last_active_at": _iso(s.last_active_at),
    }
    if message_count is not None:
        data["message_count"] = message_count
    if active_run_status is not None:
        data["active_run_status"] = active_run_status
    if messages is not None:
        data["messages"] = [message_to_dict(m) for m in messages]
    return data


def message_to_dict(m: Message) -> dict:
    return {
        "id": _u(m.id),
        "session_id": _u(m.session_id),
        "run_id": _u(m.run_id),
        "role": m.role,
        "content": m.content,
        "tool_name": m.tool_name,
        "metadata": m.metadata_,
        "created_at": _iso(m.created_at),
    }


def run_to_dict(
    r: Run,
    *,
    steps: list[RunStep] | None = None,
    tool_calls: list[ToolCall] | None = None,
) -> dict:
    public_state = {
        key: value
        for key, value in (r.state or {}).items()
        if not str(key).startswith("_") and key not in {"thinking", "reasoning_content"}
    }
    data = {
        "id": _u(r.id),
        "owner_id": _u(r.owner_id),
        "agent_id": _u(r.agent_id),
        "session_id": _u(r.session_id),
        "parent_run_id": _u(r.parent_run_id),
        "status": r.status,
        "input": LogSanitizer.sanitize_dict(r.input or {}),
        "state": LogSanitizer.sanitize_dict(public_state),
        "model_usage": r.model_usage,
        "cost": r.cost,
        "error": r.error,
        "started_at": _iso(r.started_at),
        "completed_at": _iso(r.completed_at),
        "created_at": _iso(r.created_at),
    }
    if steps is not None:
        data["steps"] = [step_to_dict(s) for s in steps]
    if tool_calls is not None:
        data["tool_calls"] = [tool_call_to_dict(t) for t in tool_calls]
    return data


def step_to_dict(s: RunStep) -> dict:
    return {
        "id": _u(s.id),
        "run_id": _u(s.run_id),
        "step_type": s.step_type,
        "status": s.status,
        "data": LogSanitizer.sanitize_dict(s.data or {}),
        "started_at": _iso(s.started_at),
        "completed_at": _iso(s.completed_at),
    }


def tool_call_to_dict(t: ToolCall) -> dict:
    return {
        "id": _u(t.id),
        "run_id": _u(t.run_id),
        "tool_name": t.tool_name,
        "arguments": LogSanitizer.sanitize_dict(t.arguments or {}),
        "risk_level": t.risk_level,
        "approval_id": _u(t.approval_id),
        "status": t.status,
        "result": LogSanitizer.sanitize_dict(t.result or {}) if t.result else None,
        "error": LogSanitizer.sanitize_dict(t.error or {}) if t.error else None,
        "idempotency_key": t.idempotency_key,
        "started_at": _iso(t.started_at),
        "completed_at": _iso(t.completed_at),
    }


def approval_to_dict(a: Approval) -> dict:
    return {
        "id": _u(a.id),
        "run_id": _u(a.run_id),
        "session_id": _u(a.session_id),
        "owner_id": _u(a.owner_id),
        "action_summary": a.action_summary,
        "tool_name": a.tool_name,
        "arguments_preview": LogSanitizer.sanitize_dict(a.arguments_preview or {}),
        "risk_level": a.risk_level,
        "risk_reason": a.risk_reason,
        "argument_hash": a.argument_hash,
        "requires_auth_method": a.requires_auth_method,
        "status": a.status,
        "approved_by": _u(a.approved_by),
        "expires_at": _iso(a.expires_at),
        "created_at": _iso(a.created_at),
    }


def memory_to_dict(m: MemoryRow) -> dict:
    return {
        "id": _u(m.id),
        "owner_id": _u(m.owner_id),
        "type": m.type,
        "scope": m.scope,
        "content": m.content,
        "summary": m.summary,
        "importance": m.importance,
        "confidence": m.confidence,
        "source_type": m.source_type,
        "source_id": m.source_id,
        "sensitivity": m.sensitivity,
        "status": m.status,
        "created_at": _iso(m.created_at),
        "updated_at": _iso(m.updated_at),
    }


def automation_to_dict(a: Automation) -> dict:
    return {
        "id": _u(a.id),
        "owner_id": _u(a.owner_id),
        "name": a.name,
        "description": a.description,
        "trigger_type": a.trigger_type,
        "trigger_config": a.trigger_config,
        "prompt": a.prompt,
        "context_policy": a.context_policy,
        "notify_channel": a.notify_channel,
        "status": a.status,
        "enabled": a.enabled,
        "last_run_at": _iso(a.last_run_at),
        "next_run_at": _iso(a.next_run_at),
        "created_at": _iso(a.created_at),
    }


def audit_to_dict(e: AuditEvent) -> dict:
    return {
        "id": _u(e.id),
        "owner_id": _u(e.owner_id),
        "actor_type": e.actor_type,
        "actor_id": e.actor_id,
        "event_type": e.event_type,
        "resource_type": e.resource_type,
        "resource_id": e.resource_id,
        "details": e.details,
        "created_at": _iso(e.created_at),
    }
