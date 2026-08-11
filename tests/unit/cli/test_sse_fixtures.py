"""T03 — SSE fixture sanity tests.

Verify every fixture is well-formed SSE (parses to event+valid-JSON pairs) and
document the event-type coverage the fixtures provide for Phase 1's decoder.
"""

from __future__ import annotations

import pytest

from tests.unit.cli import sse_fixtures


@pytest.mark.parametrize("name", sse_fixtures._ALL_FIXTURES)
def test_every_fixture_parses(name: str):
    body = sse_fixtures.load_raw(name)
    events = sse_fixtures.parse_sse(body)
    assert events, f"{name}: fixture must contain at least one event"
    # every parsed payload is valid JSON by construction; assert it's a dict
    for _, payload in events:
        assert isinstance(payload, dict)


def test_simple_complete_events():
    events = sse_fixtures.parse_sse(sse_fixtures.load_raw("simple_complete"))
    assert [e for e, _ in events] == [
        "run.started",
        "text.delta",
        "text.delta",
        "run.completed",
    ]


def test_comments_are_skipped():
    events = sse_fixtures.parse_sse(sse_fixtures.load_raw("comments_heartbeat"))
    types = [e for e, _ in events]
    assert "ping" not in types
    assert types[0] == "run.started"


def test_multiline_data_joined():
    events = sse_fixtures.parse_sse(sse_fixtures.load_raw("multiline_data"))
    assert events[0][1] == {"run_id": "r1", "status": "running"}


def test_fixture_coverage_has_required_categories():
    """The fixtures must cover the event categories the normalizer depends on."""
    all_events = set()
    for name in sse_fixtures._ALL_FIXTURES:
        for event, _ in sse_fixtures.parse_sse(sse_fixtures.load_raw(name)):
            all_events.add(event)
    for required in (
        "run.started",
        "run.completed",
        "text.delta",
        "thinking.delta",
        "tool.requested",
        "tool.failed",
        "approval.required",
    ):
        assert required in all_events, f"missing fixture coverage for {required}"
