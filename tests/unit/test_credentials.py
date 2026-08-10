"""Tests for the credential broker: injection, errors, no secret leakage."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from personal_ai_os.common.models import AuthError, ToolDescriptor
from personal_ai_os.common.utils import LogSanitizer
from personal_ai_os.policy_engine import CredentialBroker


def github_tool(scope: list[str] | None = None) -> ToolDescriptor:
    return ToolDescriptor(
        name="github.status",
        namespace="github",
        description="GitHub status",
        input_schema={"type": "object"},
        credential_scope=scope or ["github.token"],
    )


async def test_inject_injects_token():
    broker = CredentialBroker(source={"github.token": "fake-token"})
    args = {"repo": "zju/personal-ai-os"}

    injected = await broker.inject(github_tool(), args, owner_id=uuid4())
    # original dict untouched
    assert args == {"repo": "zju/personal-ai-os"}
    assert injected["repo"] == "zju/personal-ai-os"
    assert injected["_secrets"]["github.token"]["token"] == "fake-token"


async def test_secret_ref_never_contains_value():
    broker = CredentialBroker(source={"github.token": "fake-token"})
    ref = broker.secret_ref("github.token")
    assert ref == "source:github.token"
    assert "fake-token" not in ref
    assert broker.secret_ref("github.write") is None


async def test_missing_scope_raises_auth_error():
    broker = CredentialBroker(source={})
    with pytest.raises(AuthError, match="github.token"):
        await broker.inject(github_tool(), {"repo": "x"}, owner_id=uuid4())


async def test_agent_never_sees_raw_secret_in_sanitized_output():
    broker = CredentialBroker(source={"github.token": "fake-token"})
    injected = await broker.inject(github_tool(), {"repo": "x"}, owner_id=uuid4())

    # the raw secret must not survive log sanitization
    sanitized = LogSanitizer.sanitize_dict(injected)
    assert "fake-token" not in json.dumps(sanitized, ensure_ascii=False)
    # and it is not part of the public (non-hidden) arguments
    public = {k: v for k, v in injected.items() if not k.startswith("_")}
    assert "fake-token" not in json.dumps(public, ensure_ascii=False)


async def test_env_fallback(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-secret-abc")
    broker = CredentialBroker(source={})
    injected = await broker.inject(github_tool(), {}, owner_id=uuid4())
    assert injected["_secrets"]["github.token"]["token"] == "env-secret-abc"
    assert broker.secret_ref("github.token") == "env:GITHUB_TOKEN"


async def test_multiple_scopes_injected():
    broker = CredentialBroker(source={"github.token": "t1", "github.username": "octocat"})
    tool = ToolDescriptor(
        name="github.push",
        namespace="github",
        description="push",
        input_schema={"type": "object"},
        credential_scope=["github.token", "github.username"],
    )
    injected = await broker.inject(tool, {}, owner_id=uuid4())
    assert injected["_secrets"]["github.token"]["token"] == "t1"
    assert injected["_secrets"]["github.username"]["token"] == "octocat"


async def test_no_credential_scope_is_noop():
    broker = CredentialBroker(source={"github.token": "t"})
    tool = ToolDescriptor(
        name="calc",
        namespace="core",
        description="calc",
        input_schema={"type": "object"},
        credential_scope=[],
    )
    injected = await broker.inject(tool, {"expression": "1+1"}, owner_id=uuid4())
    assert injected == {"expression": "1+1"}
