"""Shared core data models for Personal AI OS.

This is the single source of truth for the cross-department contracts.
All departments implement against these models and protocols.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, TypedDict
from uuid import UUID, uuid4


# ---------------------------------------------------------------------------
# Trust labels
# ---------------------------------------------------------------------------


class TrustLevel(str, Enum):
    TRUSTED_USER = "trusted_user"
    TRUSTED_SYSTEM = "trusted_system"
    TRUSTED_TOOL = "trusted_tool"

    UNTRUSTED_WEB = "untrusted_web"
    UNTRUSTED_EMAIL = "untrusted_email"
    UNTRUSTED_DOCUMENT = "untrusted_document"
    UNTRUSTED_MCP = "untrusted_mcp"

    @property
    def is_trusted(self) -> bool:
        return self in (TrustLevel.TRUSTED_USER, TrustLevel.TRUSTED_SYSTEM, TrustLevel.TRUSTED_TOOL)


# ---------------------------------------------------------------------------
# Errors / error taxonomy
# ---------------------------------------------------------------------------


class ErrorCode(str, Enum):
    MODEL_ERROR = "MODEL_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    POLICY_DENIED = "POLICY_DENIED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    SANDBOX_ERROR = "SANDBOX_ERROR"
    CONNECTOR_ERROR = "CONNECTOR_ERROR"
    INVALID_STATE = "INVALID_STATE"
    USER_CANCELLED = "USER_CANCELLED"


class AgentOSError(Exception):
    """Base class for all Personal AI OS errors."""

    code: ErrorCode = ErrorCode.INVALID_STATE
    recoverable: bool = False

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class ModelError(AgentOSError):
    code = ErrorCode.MODEL_ERROR
    recoverable = True


class ToolError(AgentOSError):
    code = ErrorCode.TOOL_ERROR
    recoverable = True


class AuthError(AgentOSError):
    code = ErrorCode.AUTH_ERROR
    recoverable = True


class PolicyDeniedError(AgentOSError):
    code = ErrorCode.POLICY_DENIED
    recoverable = False


class ApprovalRejectedError(AgentOSError):
    code = ErrorCode.APPROVAL_REJECTED
    recoverable = False


class ApprovalRequiredError(AgentOSError):
    """Raised when a tool call requires HITL approval. Caught by the Runtime."""

    code = ErrorCode.APPROVAL_REJECTED  # reuses taxonomy; recoverable via approval
    recoverable = True

    def __init__(self, *, request_id: UUID, tool_name: str, risk_level: int, reason: str):
        super().__init__(f"Approval required for {tool_name}: {reason}")
        self.request_id = request_id
        self.tool_name = tool_name
        self.risk_level = risk_level
        self.reason = reason


class TimeoutError_(AgentOSError):
    code = ErrorCode.TIMEOUT
    recoverable = True


class RateLimitError(AgentOSError):
    code = ErrorCode.RATE_LIMIT
    recoverable = True


class ConnectorError(AgentOSError):
    code = ErrorCode.CONNECTOR_ERROR
    recoverable = True


# ---------------------------------------------------------------------------
# Tool system
# ---------------------------------------------------------------------------


class ToolDescriptor:
    """Unified tool description. Plain class (not pydantic) to keep it import-light."""

    __slots__ = (
        "name", "namespace", "description", "input_schema", "output_schema",
        "source", "source_ref", "risk_level", "side_effect", "destructive",
        "external_write", "idempotent", "credential_scope", "timeout_seconds",
        "retry_policy", "tags", "version",
    )

    def __init__(
        self,
        *,
        name: str,
        namespace: str,
        description: str,
        input_schema: dict,
        output_schema: dict | None = None,
        source: str = "native",
        source_ref: str | None = None,
        risk_level: int = 0,
        side_effect: bool = False,
        destructive: bool = False,
        external_write: bool = False,
        idempotent: bool = False,
        credential_scope: list[str] | None = None,
        timeout_seconds: int = 30,
        retry_policy: str | None = None,
        tags: list[str] | None = None,
        version: str = "0.1.0",
    ):
        self.name = name
        self.namespace = namespace
        self.description = description
        self.input_schema = input_schema
        self.output_schema = output_schema
        self.source = source
        self.source_ref = source_ref
        self.risk_level = risk_level
        self.side_effect = side_effect
        self.destructive = destructive
        self.external_write = external_write
        self.idempotent = idempotent
        self.credential_scope = credential_scope or []
        self.timeout_seconds = timeout_seconds
        self.retry_policy = retry_policy
        self.tags = tags or []
        self.version = version

    def to_llm_schema(self) -> dict:
        """Return the OpenAI-style function schema for LLM injection."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "description": self.description,
            "source": self.source,
            "risk_level": self.risk_level,
            "side_effect": self.side_effect,
            "destructive": self.destructive,
            "external_write": self.external_write,
            "idempotent": self.idempotent,
            "timeout_seconds": self.timeout_seconds,
            "tags": list(self.tags),
            "version": self.version,
        }

    def __repr__(self) -> str:
        return f"ToolDescriptor(name={self.name!r}, risk={self.risk_level})"


class ToolExecutionContext:
    __slots__ = ("run_id", "session_id", "owner_id", "agent_id", "idempotency_key",
                 "approved_by", "approval_id")

    def __init__(
        self,
        *,
        run_id: UUID,
        session_id: UUID | None = None,
        owner_id: UUID,
        agent_id: UUID | None = None,
        idempotency_key: str | None = None,
        approved_by: UUID | None = None,
        approval_id: UUID | None = None,
    ):
        self.run_id = run_id
        self.session_id = session_id
        self.owner_id = owner_id
        self.agent_id = agent_id
        self.idempotency_key = idempotency_key or ""
        self.approved_by = approved_by
        self.approval_id = approval_id


class ToolResult:
    __slots__ = ("success", "data", "text", "error", "error_code",
                 "latency_ms", "truncated", "raw_size_bytes")

    def __init__(
        self,
        *,
        success: bool,
        data: dict | None = None,
        text: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
        latency_ms: int = 0,
        truncated: bool = False,
        raw_size_bytes: int = 0,
    ):
        self.success = success
        self.data = data
        self.text = text
        self.error = error
        self.error_code = error_code
        self.latency_ms = latency_ms
        self.truncated = truncated
        self.raw_size_bytes = raw_size_bytes

    @classmethod
    def ok(cls, *, data: dict | None = None, text: str | None = None, latency_ms: int = 0) -> "ToolResult":
        return cls(success=True, data=data, text=text, latency_ms=latency_ms)

    @classmethod
    def fail(cls, *, error: str, error_code: str = "TOOL_ERROR", latency_ms: int = 0) -> "ToolResult":
        return cls(success=False, error=error, error_code=error_code, latency_ms=latency_ms)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "text": self.text,
            "error": self.error,
            "error_code": self.error_code,
            "latency_ms": self.latency_ms,
            "truncated": self.truncated,
        }

    def __repr__(self) -> str:
        return f"ToolResult(success={self.success}, error={self.error!r})"


# ---------------------------------------------------------------------------
# Policy / approval
# ---------------------------------------------------------------------------


Decision = Literal["allow", "ask", "deny"]


class PolicyDecision:
    __slots__ = ("decision", "risk_level", "reasons", "constraints", "requires_approval_receipt")

    def __init__(
        self,
        *,
        decision: Decision,
        risk_level: int,
        reasons: list[str] | None = None,
        constraints: dict | None = None,
        requires_approval_receipt: bool = False,
    ):
        self.decision = decision
        self.risk_level = risk_level
        self.reasons = reasons or []
        self.constraints = constraints or {}
        self.requires_approval_receipt = requires_approval_receipt

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "risk_level": self.risk_level,
            "reasons": list(self.reasons),
            "constraints": dict(self.constraints),
        }

    def __repr__(self) -> str:
        return f"PolicyDecision({self.decision}, risk={self.risk_level})"


class ApprovalRequest:
    __slots__ = ("id", "run_id", "session_id", "owner_id", "action_summary", "tool_name",
                 "arguments_preview", "risk_level", "risk_reason", "requires_auth_method",
                 "expires_at", "status", "created_at")

    def __init__(
        self,
        *,
        id: UUID | None = None,
        run_id: UUID,
        session_id: UUID | None,
        owner_id: UUID,
        action_summary: str,
        tool_name: str,
        arguments_preview: dict,
        risk_level: int,
        risk_reason: str = "",
        requires_auth_method: str | None = None,
        expires_at: datetime | None = None,
        status: str = "pending",
        created_at: datetime | None = None,
    ):
        self.id = id or uuid4()
        self.run_id = run_id
        self.session_id = session_id
        self.owner_id = owner_id
        self.action_summary = action_summary
        self.tool_name = tool_name
        self.arguments_preview = arguments_preview
        self.risk_level = risk_level
        self.risk_reason = risk_reason
        self.requires_auth_method = requires_auth_method
        self.expires_at = expires_at
        self.status = status
        self.created_at = created_at or datetime.utcnow()

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "run_id": str(self.run_id),
            "session_id": str(self.session_id) if self.session_id else None,
            "owner_id": str(self.owner_id),
            "action_summary": self.action_summary,
            "tool_name": self.tool_name,
            "arguments_preview": self.arguments_preview,
            "risk_level": self.risk_level,
            "risk_reason": self.risk_reason,
            "requires_auth_method": self.requires_auth_method,
            "status": self.status,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat(),
        }


class ApprovalReceipt:
    __slots__ = ("id", "approval_id", "approved_by", "decision", "scope",
                 "tool_name", "argument_hash", "expires_at", "created_at")

    def __init__(
        self,
        *,
        id: UUID | None = None,
        approval_id: UUID,
        approved_by: UUID,
        decision: str,
        scope: str,
        tool_name: str,
        argument_hash: str,
        expires_at: datetime | None = None,
    ):
        self.id = id or uuid4()
        self.approval_id = approval_id
        self.approved_by = approved_by
        self.decision = decision
        self.scope = scope
        self.tool_name = tool_name
        self.argument_hash = argument_hash
        self.expires_at = expires_at
        self.created_at = datetime.utcnow()

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "approval_id": str(self.approval_id),
            "approved_by": str(self.approved_by),
            "decision": self.decision,
            "scope": self.scope,
            "tool_name": self.tool_name,
            "argument_hash": self.argument_hash,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Model gateway
# ---------------------------------------------------------------------------


class ModelRequest:
    __slots__ = ("purpose", "messages", "tools", "latency_class", "quality_class",
                 "max_cost", "preferred_provider", "preferred_model",
                 "temperature", "max_tokens", "response_format")

    def __init__(
        self,
        *,
        purpose: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        latency_class: str = "normal",
        quality_class: str = "medium",
        max_cost: float | None = None,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ):
        self.purpose = purpose
        self.messages = messages
        self.tools = tools
        self.latency_class = latency_class
        self.quality_class = quality_class
        self.max_cost = max_cost
        self.preferred_provider = preferred_provider
        self.preferred_model = preferred_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.response_format = response_format


class ModelUsage:
    __slots__ = ("input_tokens", "cached_tokens", "output_tokens",
                 "total_tokens", "cost_usd")

    def __init__(self, *, input_tokens: int = 0, cached_tokens: int = 0,
                 output_tokens: int = 0, cost_usd: float = 0.0):
        self.input_tokens = input_tokens
        self.cached_tokens = cached_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + cached_tokens + output_tokens
        self.cost_usd = cost_usd

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "cached_tokens": self.cached_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }


class ModelResponse:
    __slots__ = ("content", "tool_calls", "model", "provider", "usage",
                 "finish_reason", "latency_ms")

    def __init__(
        self,
        *,
        content: str | None,
        tool_calls: list[dict] | None,
        model: str,
        provider: str,
        usage: ModelUsage | None = None,
        finish_reason: str = "stop",
        latency_ms: int = 0,
    ):
        self.content = content
        self.tool_calls = tool_calls
        self.model = model
        self.provider = provider
        self.usage = usage or ModelUsage()
        self.finish_reason = finish_reason
        self.latency_ms = latency_ms


class ModelStreamEvent:
    __slots__ = ("type", "text", "tool_call", "usage")

    def __init__(self, *, type: str, text: str | None = None,
                 tool_call: dict | None = None, usage: ModelUsage | None = None):
        self.type = type
        self.text = text
        self.tool_call = tool_call
        self.usage = usage


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class MemoryCreate:
    __slots__ = ("owner_id", "agent_id", "type", "scope", "scope_id", "content",
                 "summary", "importance", "confidence", "source_type", "source_id",
                 "source_event_id", "sensitivity")

    def __init__(
        self,
        *,
        owner_id: UUID,
        agent_id: UUID | None = None,
        type: str,
        scope: str,
        scope_id: UUID | None = None,
        content: str,
        summary: str | None = None,
        importance: float = 0.5,
        confidence: float = 0.5,
        source_type: str = "conversation",
        source_id: str | None = None,
        source_event_id: UUID | None = None,
        sensitivity: str = "personal",
    ):
        self.owner_id = owner_id
        self.agent_id = agent_id
        self.type = type
        self.scope = scope
        self.scope_id = scope_id
        self.content = content
        self.summary = summary
        self.importance = importance
        self.confidence = confidence
        self.source_type = source_type
        self.source_id = source_id
        self.source_event_id = source_event_id
        self.sensitivity = sensitivity


class Memory:
    __slots__ = ("id", "owner_id", "type", "scope", "content", "summary",
                 "importance", "confidence", "source_type", "source_id",
                 "sensitivity", "status", "created_at", "score")

    def __init__(
        self,
        *,
        id: UUID,
        owner_id: UUID,
        type: str,
        scope: str,
        content: str,
        summary: str | None,
        importance: float,
        confidence: float,
        source_type: str,
        source_id: str | None,
        sensitivity: str,
        status: str,
        created_at: datetime,
        score: float | None = None,
    ):
        self.id = id
        self.owner_id = owner_id
        self.type = type
        self.scope = scope
        self.content = content
        self.summary = summary
        self.importance = importance
        self.confidence = confidence
        self.source_type = source_type
        self.source_id = source_id
        self.sensitivity = sensitivity
        self.status = status
        self.created_at = created_at
        self.score = score

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "owner_id": str(self.owner_id),
            "type": self.type,
            "scope": self.scope,
            "content": self.content,
            "summary": self.summary,
            "importance": self.importance,
            "confidence": self.confidence,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "sensitivity": self.sensitivity,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "score": self.score,
        }


class MemoryQuery:
    __slots__ = ("owner_id", "query", "scope", "types", "limit", "min_confidence")

    def __init__(
        self,
        *,
        owner_id: UUID,
        query: str,
        scope: str | None = None,
        types: list[str] | None = None,
        limit: int = 10,
        min_confidence: float = 0.0,
    ):
        self.owner_id = owner_id
        self.query = query
        self.scope = scope
        self.types = types
        self.limit = limit
        self.min_confidence = min_confidence


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


class ContextItem:
    __slots__ = ("content", "source", "trust", "type", "metadata")

    def __init__(self, *, content: str, source: str, trust: str,
                 type: str = "memory", metadata: dict | None = None):
        self.content = content
        self.source = source
        self.trust = trust
        self.type = type
        self.metadata = metadata or {}


# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------


class AgentState(TypedDict, total=False):
    run_id: str
    session_id: str
    owner_id: str
    agent_id: str

    user_input: str

    messages: list[dict]
    context_items: list[dict]

    task: dict
    plan: list[dict]
    current_step: int

    pending_tool_call: dict
    tool_results: list[dict]

    pending_approval: dict

    memory_candidates: list[dict]
    skill_candidates: list[dict]

    status: str
    error: dict

    model_usage: dict


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class DomainEvent:
    __slots__ = ("id", "type", "timestamp", "owner_id", "run_id", "session_id", "payload")

    def __init__(self, *, type: str, owner_id: UUID, run_id: UUID | None = None,
                 session_id: UUID | None = None, payload: dict | None = None,
                 id: UUID | None = None, timestamp: datetime | None = None):
        self.id = id or uuid4()
        self.type = type
        self.timestamp = timestamp or datetime.utcnow()
        self.owner_id = owner_id
        self.run_id = run_id
        self.session_id = session_id
        self.payload = payload or {}

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "type": self.type,
            "timestamp": self.timestamp.isoformat(),
            "owner_id": str(self.owner_id),
            "run_id": str(self.run_id) if self.run_id else None,
            "session_id": str(self.session_id) if self.session_id else None,
            "payload": self.payload,
        }


class EventTypes:
    MESSAGE_RECEIVED = "message.received"

    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"

    TOOL_REQUESTED = "tool.requested"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_DENIED = "tool.denied"

    APPROVAL_CREATED = "approval.created"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"

    MEMORY_CREATED = "memory.created"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_DELETED = "memory.deleted"

    AUTOMATION_TRIGGERED = "automation.triggered"
    AUTOMATION_COMPLETED = "automation.completed"


# ---------------------------------------------------------------------------
# Inbound message (gateway)
# ---------------------------------------------------------------------------


class Attachment:
    __slots__ = ("type", "url", "mime_type", "filename")

    def __init__(self, *, type: str, url: str | None = None,
                 mime_type: str | None = None, filename: str | None = None):
        self.type = type
        self.url = url
        self.mime_type = mime_type
        self.filename = filename


class InboundMessage:
    __slots__ = ("event_id", "channel", "channel_account_id", "sender_id",
                 "owner_id", "conversation_key", "text", "attachments",
                 "reply_to", "timestamp", "metadata")

    def __init__(
        self,
        *,
        channel: str,
        channel_account_id: str,
        sender_id: str,
        owner_id: UUID,
        conversation_key: str,
        text: str | None = None,
        attachments: list[Attachment] | None = None,
        reply_to: str | None = None,
        timestamp: datetime | None = None,
        metadata: dict | None = None,
        event_id: UUID | None = None,
    ):
        self.event_id = event_id or uuid4()
        self.channel = channel
        self.channel_account_id = channel_account_id
        self.sender_id = sender_id
        self.owner_id = owner_id
        self.conversation_key = conversation_key
        self.text = text
        self.attachments = attachments or []
        self.reply_to = reply_to
        self.timestamp = timestamp or datetime.utcnow()
        self.metadata = metadata or {}
