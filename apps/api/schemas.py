"""Pydantic request/response schemas for the REST API."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    project_id: UUID | None = None
    title: str | None = Field(default=None, max_length=200)
    channel: str = Field(default="api", max_length=32)


class SessionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    project_id: UUID | None = None
    status: Literal["active", "paused", "archived"] | None = None


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class MessageCreate(BaseModel):
    text: str = Field(..., max_length=200_000)
    attachments: list[dict] | None = None


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunResumeBody(BaseModel):
    approval_id: UUID
    decision: str | None = None
    edited_arguments: dict | None = None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class ProviderReloadBody(BaseModel):
    config_dir: str = Field(..., min_length=1, max_length=4096)


class ProviderModelUpdateBody(ProviderReloadBody):
    model: str = Field(..., min_length=1, max_length=200)
    reasoning_effort: Literal["auto", "low", "medium", "high"] = "auto"


# ---------------------------------------------------------------------------
# Memories
# ---------------------------------------------------------------------------


class MemoryCreateBody(BaseModel):
    type: str
    scope: str = "global"
    content: str
    summary: str | None = None
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    source_type: str = "manual"
    sensitivity: str = "personal"


class MemoryUpdateBody(BaseModel):
    content: str | None = None
    summary: str | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: Literal["active", "draft", "superseded", "forgotten"] | None = None


class MemorySearchBody(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=100)
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
