"""P0-008 regression — raw chain-of-thought must not persist to public state.

reasoning_content is required by the provider protocol during the run (DeepSeek
replay), but must never land in Run.state, the API, or SSE replay.
"""

from __future__ import annotations

import uuid

import pytest


def test_strip_reasoning_removes_cot_and_thinking():
    from personal_ai_os.agent_runtime.runner import _strip_reasoning

    state = {
        "thinking": "SECRET_COT_THINKING",
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "fs.list", "arguments": {"path": "."}},
                        "reasoning_content": "SECRET_COT",
                    }
                ],
            }
        ],
        "status": "observing",
    }
    stripped = _strip_reasoning(state)
    assert "thinking" not in stripped
    assert stripped["messages"][0]["tool_calls"][0].get("reasoning_content") is None
    assert "SECRET_COT" not in str(stripped)
    assert "SECRET_COT_THINKING" not in str(stripped)
    # the in-memory copy keeps the provider protocol field untouched
    assert state["messages"][0]["tool_calls"][0]["reasoning_content"] == "SECRET_COT"
    assert state["thinking"] == "SECRET_COT_THINKING"


@pytest.mark.asyncio
async def test_persisted_run_state_has_no_reasoning_content():
    """A full run whose model emits reasoning_content must not leak it to DB."""
    from sqlalchemy import select

    from connectors import get_builtin_connectors
    from personal_ai_os.db.models import Run, User
    from personal_ai_os.db.session import session_scope
    from tests.integration.test_vertical_slice import _make_session, build_stack

    script = [
        {
            "tool_calls": [
                {
                    "type": "function",
                    "id": "call-reason-1",
                    "function": {"name": "calculator.evaluate", "arguments": {"expression": "2 + 2"}},
                    "reasoning_content": "SECRET_COT",
                }
            ]
        },
        {"content": "答案是 4", "reasoning_content": "SECRET_COT_ANSWER"},
    ]
    runner, _, broker, *_ = await build_stack(script)
    for connector in get_builtin_connectors():
        await broker.register_connector(connector)

    async with session_scope() as s:
        owner_id = (await s.execute(select(User))).scalars().first().id
    session_id = await _make_session(owner_id)

    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="2+2?")
    assert result["status"] == "completed"

    async with session_scope() as s:
        run = await s.get(Run, uuid.UUID(result["id"]))
        assert run is not None
        serialized = str(run.state)
        assert "SECRET_COT" not in serialized
        assert "reasoning_content" not in serialized
        assert "thinking" not in (run.state or {})

    # the persisted ToolCall.result must not carry the reasoning either
    from personal_ai_os.db.models import ToolCall
    from personal_ai_os.db.session import session_scope as _ss

    async with _ss() as s:
        calls = (await s.execute(select(ToolCall).where(ToolCall.run_id == uuid.UUID(result["id"])))).scalars().all()
        assert calls, "expected a persisted ToolCall"
        assert all("SECRET_COT" not in str(c.result or {}) for c in calls)
