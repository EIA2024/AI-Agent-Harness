"""SSE streaming endpoint — replays persisted run steps as events."""

from __future__ import annotations

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
async def test_stream_unknown_run(make_api):
    async with make_api(services=ServiceContainer()) as ac:
        async with ac.stream("GET", f"/v1/runs/{uuid.uuid4()}/stream") as resp:
            body = "".join([t async for t in resp.aiter_text()])

    assert "Run not found" in body
