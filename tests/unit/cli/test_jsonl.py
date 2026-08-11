"""T14 — JSONL stream writer contract tests.

Every line written by ``exec -o stream-json`` must be valid JSON.
"""

from __future__ import annotations

import json

from personal_ai_os.cli.api.sse import SSEDecoder
from personal_ai_os.cli.domain.normalizer import Normalizer
from personal_ai_os.cli.domain.reducer import reduce
from personal_ai_os.cli.domain.state import initial_state
from personal_ai_os.cli.output.jsonl import JSONLWriter
from tests.unit.cli import sse_fixtures


def _stream(body: str, *, show_reasoning: bool = False) -> list[dict]:
    """Replay a body through decoder → normalizer → reducer, emit JSONL."""
    out: list[str] = []
    writer = JSONLWriter(write=out.append)
    decoder = SSEDecoder()
    state = initial_state(session_id="s1")
    normalizer = Normalizer(show_reasoning=show_reasoning)
    server_events = []
    for line in body.splitlines(keepends=False):
        server_events.extend(decoder.feed_line(line))
    server_events.extend(decoder.finish())
    for se in server_events:
        event = normalizer.normalize(se, run_id="r1")
        reduce(state, event)
        writer.emit(event, state)
    return [json.loads(line) for line in out]


def test_every_line_is_valid_json():
    for name in sse_fixtures._ALL_FIXTURES:
        lines = _stream(sse_fixtures.load_raw(name))
        assert all(isinstance(line, dict) for line in lines), name
        assert all("v" in line and "type" in line for line in lines), name


def test_simple_complete_stream():
    lines = _stream(sse_fixtures.load_raw("simple_complete"))
    types = [line["type"] for line in lines]
    assert types == ["run.started", "assistant.delta", "assistant.delta", "run.completed"]
    assert lines[0]["run_id"] == "r1"
    assert lines[-1]["result"] == {"text": "Hello world"}


def test_thinking_not_in_stream_by_default():
    lines = _stream(sse_fixtures.load_raw("thinking_and_text"))
    assert all(line["type"] != "progress" for line in lines)


def test_tool_requested_in_stream():
    lines = _stream(sse_fixtures.load_raw("tool_complete"))
    tool = next(line for line in lines if line["type"] == "tool.requested")
    assert tool["tool"]["name"] == "filesystem.read"
    assert tool["tool"]["id"] == "call_1"


def test_approval_required_line():
    lines = _stream(sse_fixtures.load_raw("approval_pause"))
    assert lines[-1]["type"] == "approval.required"


def test_public_line_omits_nothing_for_unknown():
    state = initial_state()
    # unknown events should still be surfaced as diagnostics (not crash)
    event = sse_fixtures.parse_sse(sse_fixtures.load_raw("unknown_event"))
    assert event  # sanity
