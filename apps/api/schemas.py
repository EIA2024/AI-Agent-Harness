"""Pydantic request/response schemas for the REST API."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    project_id: UUID | None = None
    title: str | None = None
    channel: str = "api"


class SessionUpdate(BaseModel):
    title: str | None = None
    project_id: UUID | None = None
    status: str | None = None


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class MessageCreate(BaseModel):
    text: str
    attachments: list[dict] | None = None


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunResumeBody(BaseModel):
    approval_id: UUID | None = None
    decision: str | None = None
    edited_arguments: dict | None = None


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------


class MemoryCreateBody(BaseModel):
    type: str
    scope: str = "global"
    content: str
    summary: str | None = None
    importance: float = 0.5
    confidence: float = 0.5
    source_type: str = "manual"
    sensitivity: str = "personal"


class MemoryUpdateBody(BaseModel):
    content: str | None = None
    summary: str | None = None
    importance: float | None = None
    confidence: float | None = None
    status: str | None = None


class MemorySearchBody(BaseModel):
    query: str
    limit: int = 10
    scope: str | None = None


# ---------------------------------------------------------------------------
# Automations
# ---------------------------------------------------------------------------


class AutomationCreate(BaseModel):
    name: str
    description: str | None = None
    trigger_type: str = "manual"
    trigger_config: dict = Field(default_factory=dict)
    prompt: str
    notify_channel: str | None = None


class AutomationUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    trigger_type: str | None = None
    trigger_config: dict | None = None
    prompt: str | None = None
    status: str | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


class ApprovalEditBody(BaseModel):
    edited_arguments: dict
