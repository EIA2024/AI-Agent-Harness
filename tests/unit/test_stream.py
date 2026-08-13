"""SSE streaming endpoint — replays persisted run steps as events."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from personal_ai_os.db.models import Run, RunStep, User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer


async def _make_user_and_run(*, status: str = "completed") -> tuple[User, Run]:
    async with session_scope() as s:
        u = User(username=f"u{uuid.uuid4().hex[:8]}", api_key=f"k{uuid.uuid4().hex[:8]}")
        s.add(u)
        await s.flush()
        run = Run(
            owner_id=u.id,
            status=status,
            input={"text": "hi"},
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC) if status == "completed" else None,
        )
        s.add(run)
        await s.flush()
        s.add(RunStep(run_id=run.id, step_type="decide", status="completed", started_at=datetime.now(UTC)))
        s.add(
            RunStep(
                run_id=run.id,
                step_type="tool",
                status="completed",
                data={"tool_name": "web.search"},
                started_at=datetime.now(UTC),
            )
        )
        s.add(RunStep(run_id=run.id, step_type="respond", status="completed", started_at=datetime.now(UTC)))
        await s.flush()
        await s.refresh(run)
        return u, run


async def _collect_stream(ac, url: str, headers: dict) -> tuple[int, str]:
    async with ac.stream("GET", url, headers=headers) as resp:
        chunks: list[str] = []
        async for text in resp.aiter_text():
            chunks.append(text)
        return resp.status_code, "".join(chunks)


@pytest.mark.asyncio
async def test_stream_replays_events(make_api, db):
    u, run = await _make_user_and_run()
    headers = {"X-API-Key": u.api_key}

    async with make_api(services=ServiceContainer()) as ac:
        status, body = await _collect_stream(ac, f"/v1/runs/{run.id}/stream", headers)

    assert status == 200
    assert "event: run.started" in body
    assert "event: tool.completed" in body
    assert '"tool_name": "web.search"' in body
    assert "event: run.completed" in body


@pytest.mark.asyncio
async def test_stream_failed_run_emits_failed(make_api, db):
    u, run = await _make_user_and_run(status="failed")
    headers = {"X-API-Key": u.api_key}

    async with make_api(services=ServiceContainer()) as ac:
        _, body = await _collect_stream(ac, f"/v1/runs/{run.id}/stream", headers)

    assert "event: run.started" in body
    assert "event: run.failed" in body
    assert "event: run.completed" not in body


@pytest.mark.asyncio
async def test_stream_owner_isolation(make_api, db):
    u, run = await _make_user_and_run()
    other = User(username=f"u{uuid.uuid4().hex[:8]}", api_key="stream-other-key")
    async with session_scope() as s:
        s.add(other)
        await s.flush()

    async with make_api(services=ServiceContainer()) as ac:
        async with ac.stream("GET", f"/v1/runs/{run.id}/stream", headers={"X-API-Key": "stream-other-key"}) as resp:
            body = "".join([t async for t in resp.aiter_text()])

    assert "Run not found" in body  # streamed as an error event


@pytest.mark.asyncio
async def test_stream_events_have_versioned_envelope(make_api, db):
    """T41 — every SSE event carries schema_version / seq / event_id / timestamp."""
    u, run = await _make_user_and_run()
    headers = {"X-API-Key": u.api_key}

    async with make_api(services=ServiceContainer()) as ac:
        _, body = await _collect_stream(ac, f"/v1/runs/{run.id}/stream", headers)

    assert '"schema_version": 1' in body
    assert '"event_id":' in body
    assert '"seq": 1' in body
    assert '"timestamp":' in body
    assert '"run_id":' in body


@pytest.mark.asyncio
async def test_stream_approval_replay_is_enriched(make_api, db):
    """T43 — replay of a waiting_approval run surfaces the pending approval."""
    from personal_ai_os.db.models import Approval

    u = User(username=f"u{uuid.uuid4().hex[:8]}", api_key="stream-appr-key")
    headers = {"X-API-Key": "stream-appr-key"}
    async with session_scope() as s:
        s.add(u)
        await s.flush()
        run = Run(
            owner_id=u.id,
            status="waiting_approval",
            input={"text": "hi"},
            started_at=datetime.now(UTC),
        )
        s.add(run)
        await s.flush()
        s.add(
            Approval(
                run_id=run.id,
                owner_id=u.id,
                action_summary="send email",
                tool_name="mail.send",
                arguments_preview={"to": "x@y.z", "api_key": "secret-value"},
                risk_level=3,
                status="pending",
            )
        )
        await s.flush()

    async with make_api(services=ServiceContainer()) as ac:
        _, body = await _collect_stream(ac, f"/v1/runs/{run.id}/stream", headers)

    assert "event: approval.required" in body
    assert '"approval_id":' in body
    assert '"tool_name": "mail.send"' in body
    assert '"risk_level": 3' in body
    assert '"action_summary": "send email"' in body
    assert '"arguments_preview": {"to": "x@y.z", "api_key": "[REDACTED]"}' in body
    assert "secret-value" not in body


@pytest.mark.asyncio
async def test_stream_unknown_run(make_api):
    u = User(username=f"u{uuid.uuid4().hex[:8]}", api_key="stream-auth-key")
    async with session_scope() as s:
        s.add(u)
        await s.flush()
    headers = {"X-API-Key": "stream-auth-key"}
    async with make_api(services=ServiceContainer()) as ac:
        async with ac.stream("GET", f"/v1/runs/{uuid.uuid4()}/stream", headers=headers) as resp:
            body = "".join([t async for t in resp.aiter_text()])

    assert "Run not found" in body


@pytest.mark.asyncio
async def test_durable_stream_preserves_event_ids_and_resumes_from_cursor(make_api, db):
    from personal_ai_os.agent_runtime import event_log

    u, run = await _make_user_and_run()
    headers = {"X-API-Key": u.api_key}
    await event_log.append_run_event(
        run.id, "run.started", {"run_id": str(run.id), "status": "running"}
    )
    await event_log.append_run_event(
        run.id, "run.completed", {"run_id": str(run.id), "status": "completed"}
    )

    async with make_api(services=ServiceContainer()) as ac:
        _, first = await _collect_stream(ac, f"/v1/runs/{run.id}/stream", headers)
        resumed_headers = headers | {"Last-Event-ID": f"{run.id}:1"}
        _, resumed = await _collect_stream(
            ac, f"/v1/runs/{run.id}/stream", resumed_headers
        )

    assert f"id: {run.id}:1" in first
    assert f'"event_id": "{run.id}:1"' in first
    assert '"seq": 1' in first and '"seq": 2' in first
    assert "event: run.started" not in resumed
    assert "event: run.completed" in resumed
    assert f"id: {run.id}:2" in resumed


@pytest.mark.asyncio
async def test_cross_worker_stream_tails_durable_events_until_terminal(make_api, db):
    from sqlalchemy import update

    from personal_ai_os.agent_runtime import event_log

    user, run = await _make_user_and_run(status="running")
    headers = {"X-API-Key": user.api_key}

    async def finish_from_other_worker():
        await asyncio.sleep(0.15)
        await event_log.append_run_event(
            run.id, "text.delta", {"run_id": str(run.id), "text": "remote"}
        )
        async with session_scope() as session:
            await session.execute(
                update(Run).where(Run.id == run.id).values(status="completed")
            )
        await event_log.append_run_event(
            run.id, "run.completed", {"run_id": str(run.id), "status": "completed"}
        )

    task = asyncio.create_task(finish_from_other_worker())
    async with make_api(services=ServiceContainer()) as ac:
        _, body = await _collect_stream(ac, f"/v1/runs/{run.id}/stream", headers)
    await task

    assert "remote" in body
    assert "event: run.completed" in body
