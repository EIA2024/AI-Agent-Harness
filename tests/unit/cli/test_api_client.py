"""T10 — AsyncAPIClient unit tests (typed errors, DTO mapping, streaming)."""

from __future__ import annotations

import json

import httpx
import pytest

from personal_ai_os.cli.api import errors
from personal_ai_os.cli.api.client import AsyncAPIClient


def _make_client(handler) -> AsyncAPIClient:
    return AsyncAPIClient(
        base_url="http://test",
        api_key="k",
        transport=httpx.MockTransport(handler),
    )


def _json_response(body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


# ---------------------------------------------------------------------------
# error classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "exc_type"),
    [
        (401, errors.AuthError),
        (404, errors.NotFoundError),
        (409, errors.ConflictError),
        (429, errors.RateLimitError),
        (500, errors.ServerError),
        (502, errors.ServerError),
        (418, errors.APIError),
    ],
)
async def test_status_maps_to_typed_error(status, exc_type):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "nope"})

    client = _make_client(handler)
    with pytest.raises(exc_type) as exc:
        await client.request("GET", "/x")
    assert exc.value.status_code == status
    assert exc.value.detail == "nope"


async def test_retryable_flags():
    assert errors.RateLimitError(429, "x").retryable is True
    assert errors.ServerError(500, "x").retryable is True
    assert errors.AuthError(401, "x").retryable is False


async def test_transport_error_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _make_client(handler)
    with pytest.raises(errors.TransportError):
        await client.request("GET", "/x")


async def test_success_returns_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == "k"
        return _json_response({"ok": True})

    client = _make_client(handler)
    assert await client.request("GET", "/x") == {"ok": True}


# ---------------------------------------------------------------------------
# DTO mapping
# ---------------------------------------------------------------------------


async def test_create_session_dto():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return _json_response(
            {"id": "s1", "title": "New chat", "status": "active", "channel": body["channel"]}
        )

    client = _make_client(handler)
    session = await client.create_session(channel="cli")
    assert session.id == "s1"
    assert session.channel == "cli"


async def test_send_message_dto():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"run_id": "r1", "status": "running", "message_id": "m1"})

    client = _make_client(handler)
    result = await client.send_message("s1", "hello")
    assert result.run_id == "r1"
    assert result.status == "running"
    assert result.message_id == "m1"


async def test_get_run_parses_final_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {"id": "r1", "status": "completed", "state": {"final_response": "hi"}}
        )

    client = _make_client(handler)
    run = await client.get_run("r1")
    assert run.status == "completed"
    assert run.final_response == "hi"


async def test_approval_dto_from_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            [
                {
                    "id": "a1",
                    "tool_name": "filesystem.read",
                    "risk_level": 1,
                    "status": "pending",
                    "action_summary": "read file",
                }
            ]
        )

    client = _make_client(handler)
    approvals = await client.list_approvals()
    assert approvals[0].id == "a1"
    assert approvals[0].risk_level == 1


# ---------------------------------------------------------------------------
# streaming
# ---------------------------------------------------------------------------


def _sse_body() -> str:
    return (
        "event: run.started\n"
        'data: {"run_id": "r1"}\n'
        "\n"
        "event: text.delta\n"
        'data: {"text": "hi"}\n'
        "\n"
        "event: run.completed\n"
        'data: {"run_id": "r1", "status": "completed"}\n'
        "\n"
    )


async def test_stream_run_yields_decoded_events():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_sse_body())

    client = _make_client(handler)
    events = [e async for e in client.stream_run("r1")]
    assert [e.event for e in events] == ["run.started", "text.delta", "run.completed"]
    assert events[-1].data == {"run_id": "r1", "status": "completed"}


async def test_stream_run_raises_typed_error_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "unknown run"})

    client = _make_client(handler)
    with pytest.raises(errors.NotFoundError):
        async for _ in client.stream_run("missing"):
            pass


async def test_stream_run_raises_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset")

    client = _make_client(handler)
    with pytest.raises(errors.TransportError):
        async for _ in client.stream_run("r1"):
            pass


async def test_stream_gap_detected():
    """P1-019 — a seq jump (dropped events) yields a gap warning event."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            "event: run.started\n"
            'data: {"run_id": "r1", "seq": 1}\n'
            "\n"
            "event: text.delta\n"
            'data: {"text": "hi", "seq": 4}\n'  # jumped from 1 → 4
            "\n"
        )
        return httpx.Response(200, text=body)

    client = _make_client(handler)
    events = [e async for e in client.stream_run("r1")]
    assert any(e.event == "stream.gap" for e in events), events
    gap = next(e for e in events if e.event == "stream.gap")
    assert gap.data["from_seq"] == 1 and gap.data["to_seq"] == 4
