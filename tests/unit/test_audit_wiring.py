"""Audit wiring: ToolBroker writes audit events for denied + executed tools."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from personal_ai_os.common.models import ToolExecutionContext, ToolResult
from personal_ai_os.db.models import AuditEvent, User
from personal_ai_os.db.session import session_scope
from personal_ai_os.observability import AuditLogger
from personal_ai_os.policy_engine import CredentialBroker, PolicyEngine
from personal_ai_os.tool_broker import ToolBroker, ToolRegistry


class _EchoConnector:
    connector_name = "echo"

    async def list_tools(self):
        from personal_ai_os.common.models import ToolDescriptor

        return [
            ToolDescriptor(
                name="echo.safe", namespace="echo", description="safe", input_schema={"type": "object"},
                risk_level=0,
            ),
            ToolDescriptor(
                name="credential.peek", namespace="credential", description="denied",
                input_schema={"type": "object"}, risk_level=0,
            ),
        ]

    async def execute(self, tool, arguments, ctx) -> ToolResult:
        return ToolResult.ok(data={"ok": True})


@pytest.mark.asyncio
async def test_audit_written_for_execute_and_deny():
    policy = PolicyEngine()
    broker = ToolBroker(
        registry=ToolRegistry(), policy_engine=policy,
        credential_broker=CredentialBroker(source={}),
        audit_logger=AuditLogger(),
    )
    await broker.register_connector(_EchoConnector())

    async with session_scope() as s:
        u = User(username=f"au{uuid.uuid4().hex[:8]}", api_key="ak")
        s.add(u)
        await s.flush()
        owner_id = u.id

    ctx = ToolExecutionContext(run_id=uuid.uuid4(), owner_id=owner_id)

    # R0 tool -> executes -> audit "tool.executed"
    r1 = await broker.execute("echo.safe", {}, ctx)
    assert r1.success

    # credential.* -> denied by default policy -> audit "tool.denied"
    r2 = await broker.execute("credential.peek", {}, ctx)
    assert not r2.success and r2.error_code == "POLICY_DENIED"

    async with session_scope() as s:
        events = (await s.execute(select(AuditEvent).where(AuditEvent.owner_id == owner_id))).scalars().all()
        types = [e.event_type for e in events]
        assert "tool.executed" in types
        assert "tool.denied" in types
