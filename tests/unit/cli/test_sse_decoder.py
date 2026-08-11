"""T11 — SSE decoder unit tests.

The decoder must pass every fixture in ``tests/unit/cli/fixtures`` plus the
edge cases below.
"""

from __future__ import annotations

import pytest

from personal_ai_os.cli.api.sse import SSEDecoder
from tests.unit.cli import sse_fixtures


def decode(body: str):
    decoder = SSEDecoder()
    events = []
    for line in body.splitlines(keepends=False):
        events.extend(decoder.feed_line(line + "\n"))
    events.extend(decoder.finish())
    return events


@pytest.mark.parametrize("name", sse_fixtures._ALL_FIXTURES)
def test_all_fixtures_decode(name: str):
    expected = sse_fixtures.parse_sse(sse_fixtures.load_raw(name))
    actual = decode(sse_fixtures.load_raw(name))
    assert [(e.event, e.data) for e in actual] == expected


def test_simple_complete():
    events = decode(sse_fixtures.load_raw("simple_complete"))
    assert [e.event for e in events] == [
        "run.started",
        "text.delta",
        "text.delta",
        "run.completed",
    ]
    assert events[-1].data == {"run_id": "r1", "status": "completed"}


def test_comments_and_heartbeats_ignored():
    events = decode(sse_fixtures.load_raw("comments_heartbeat"))
    assert all(e.event != "ping" for e in events)


def test_multiline_data_joined_with_newline():
    events = decode(sse_fixtures.load_raw("multiline_data"))
    assert events[0].data == {"run_id": "r1", "status": "running"}


def test_malformed_json_sets_flag_not_crash():
    events = decode("event: mystery\ndata: {not json\n\n")
    assert len(events) == 1
    assert events[0].event == "mystery"
    assert events[0].malformed is True
    assert events[0].raw_data == "{not json"


def test_id_and_retry_fields():
    events = decode("id: 42\nretry: 3000\nevent: run.started\ndata: {}\n\n")
    assert len(events) == 1
    assert events[0].id == "42"
    assert events[0].retry == 3000


def test_no_data_line_emits_empty_payload():
    events = decode("event: heartbeat\n\n")
    assert len(events) == 1
    assert events[0].data == {}


def test_default_event_name_when_absent():
    events = decode('data: {"a": 1}\n\n')
    assert events[0].event == "message"
    assert events[0].data == {"a": 1}


def test_finish_flushes_trailing_event_without_blank_line():
    decoder = SSEDecoder()
    decoder.feed_line("event: run.completed\n")
    decoder.feed_line('data: {"run_id": "r1"}\n')
    flushed = decoder.finish()
    assert len(flushed) == 1
    assert flushed[0].event == "run.completed"
    assert flushed[0].data == {"run_id": "r1"}


def test_non_object_json_becomes_value_payload():
    events = decode('event: x\ndata: [1,2,3]\n\n')
    assert events[0].data == {"value": [1, 2, 3]}
