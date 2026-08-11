"""T12 — UIEvent normalizer tests.

The normalizer must map legacy/live/replay server events to stable UI events,
suppress raw thinking by default, and never crash on unknown input.
"""

from __future__ import annotations

from personal_ai_os.cli.api.sse import SSEDecoder
from personal_ai_os.cli.domain.events import UIEventType
from personal_ai_os.cli.domain.normalizer import Normalizer
from tests.unit.cli import sse_fixtures


def _events(body: str, *, show_reasoning: bool = False) -> list:
    decoder = SSEDecoder()
    server_events = []
    for line in body.splitlines(keepends=False):
        server_events.extend(decoder.feed_line(line))
    server_events.extend(decoder.finish())
    normalizer = Normalizer(show_reasoning=show_reasoning)
    return [normalizer.normalize(e, run_id="r1") for e in server_events]


def test_simple_complete_normalizes():
    events = _events(sse_fixtures.load_raw("simple_complete"))
    assert [e.type for e in events] == [
        UIEventType.RUN_STARTED,
        UIEventType.ASSISTANT_DELTA,
        UIEventType.ASSISTANT_DELTA,
        UIEventType.RUN_COMPLETED,
    ]
    assert events[-1].run_id == "r1"


def test_thinking_suppressed_by_default():
    events = _events(sse_fixtures.load_raw("thinking_and_text"))
    progress = [e for e in events if e.type == UIEventType.PROGRESS]
    assert len(progress) == 1
    assert progress[0].payload["raw_thinking"] is True
    assert progress[0].payload["shown"] is False
    # the answer delta survives untouched
    deltas = [e for e in events if e.type == UIEventType.ASSISTANT_DELTA]
    assert deltas[0].payload["text"] == "Answer"


def test_thinking_shown_when_requested():
    events = _events(
        sse_fixtures.load_raw("thinking_and_text"), show_reasoning=True
    )
    progress = [e for e in events if e.type == UIEventType.PROGRESS][0]
    assert progress.payload["shown"] is True


def test_tool_requested_parses_tool_call():
    events = _events(sse_fixtures.load_raw("tool_complete"))
    tool = next(e for e in events if e.type == UIEventType.TOOL_REQUESTED)
    assert tool.payload["tool_name"] == "filesystem.read"
    assert tool.payload["tool_call_id"] == "call_1"
    assert tool.payload["arguments"] == {"path": "src"}


def test_approval_required():
    events = _events(sse_fixtures.load_raw("approval_pause"))
    approval = next(e for e in events if e.type == UIEventType.APPROVAL_REQUIRED)
    assert approval.payload["status"] == "waiting_approval"


def test_unknown_event_becomes_unknown_not_crash():
    events = _events(sse_fixtures.load_raw("unknown_event"))
    kinds = [e.type for e in events]
    assert UIEventType.UNKNOWN in kinds
    assert UIEventType.ASSISTANT_DELTA in kinds


def test_malformed_payload_becomes_warning():
    events = _events("event: mystery\ndata: {nope\n\n")
    assert events[0].type == UIEventType.WARNING


def test_replay_node_steps_become_progress():
    events = _events(sse_fixtures.load_raw("replay_run"))
    progress = [e for e in events if e.type == UIEventType.PROGRESS]
    messages = {e.payload.get("message") for e in progress}
    assert "Building context" in messages
    # replayed final answer is still an assistant delta
    deltas = [e for e in events if e.type == UIEventType.ASSISTANT_DELTA]
    assert any(e.payload.get("text") == "Final answer" for e in deltas)


def test_replay_tool_completed():
    events = _events(sse_fixtures.load_raw("replay_run"))
    tool_done = next(e for e in events if e.type == UIEventType.TOOL_COMPLETED)
    assert tool_done.payload["tool_name"] == "filesystem.read"
    assert tool_done.payload["latency_ms"] == 420


def test_server_error_event_maps_to_error():
    """F1.2 — the server streams `error` (HTTP 200) for unknown runs."""
    events = _events('event: error\ndata: {"detail": "Run not found"}\n\n')
    assert events[0].type == UIEventType.ERROR
    assert events[0].payload["message"] == "Run not found"
