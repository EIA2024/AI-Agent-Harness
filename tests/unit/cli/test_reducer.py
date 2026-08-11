"""T13 — reducer tests: fixture replay → deterministic AppState."""

from __future__ import annotations

from datetime import UTC, datetime

from personal_ai_os.cli.domain.state import (
    CELL_APPROVAL,
    CELL_ASSISTANT,
    CELL_NOTICE,
    CELL_PROGRESS,
    CELL_RUN_STATUS,
    ConnectionState,
)
from tests.unit.cli import sse_fixtures

_FIXED_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _final_state(name: str, *, show_reasoning: bool = False):
    return sse_fixtures.reduce_fixture(
        name, show_reasoning=show_reasoning, ts=_FIXED_TS
    )


def test_simple_complete_final_state():
    state = _final_state("simple_complete")
    assert state.run_status == "completed"
    assert state.run_id == "r1"
    assert state.final_response == "Hello world"
    assert state.connection_state == ConnectionState.CLOSED
    kinds = [c.kind for c in state.transcript]
    assert kinds[0] == CELL_RUN_STATUS
    assert kinds[-1] == CELL_RUN_STATUS
    assert CELL_ASSISTANT in kinds


def test_thinking_not_in_transcript_by_default():
    state = _final_state("thinking_and_text")
    assert all(c.kind != CELL_PROGRESS for c in state.transcript)
    assert state.final_response == "Answer"


def test_thinking_cell_present_when_shown():
    state = _final_state("thinking_and_text", show_reasoning=True)
    assert any(c.kind == CELL_PROGRESS for c in state.transcript)


def test_tool_requested_creates_tool_state():
    state = _final_state("tool_complete")
    assert "call_1" in state.tool_calls
    tool = state.tool_calls["call_1"]
    assert tool.name == "filesystem.read"
    assert tool.arguments_preview == {"path": "src"}


def test_tool_failed_state():
    state = _final_state("tool_failed")
    failed = [t for t in state.tool_calls.values() if t.status == "failed"]
    assert len(failed) == 1
    assert failed[0].error == "connection_timeout"


def test_approval_pause_state():
    state = _final_state("approval_pause")
    assert state.run_status == "waiting_approval"
    assert state.pending_approval is not None
    assert any(c.kind == CELL_APPROVAL for c in state.transcript)


def test_unexpected_eof_leaves_run_running():
    state = _final_state("unexpected_eof")
    assert state.run_status == "running"
    assert state.assistant_buffer == "partial answer, connection dropped"


def test_unknown_event_does_not_crash():
    state = _final_state("unknown_event")
    assert state.run_status == "completed"
    assert any(c.kind == CELL_NOTICE for c in state.transcript)


def test_duplicate_terminal_is_idempotent():
    state = _final_state("duplicate_event")
    assert state.run_status == "completed"
    assert state.final_response == "hi"


def test_replay_run_final_state():
    state = _final_state("replay_run")
    assert state.run_status == "completed"
    assert state.final_response == "Final answer"
    completed = [t for t in state.tool_calls.values() if t.status == "completed"]
    assert len(completed) == 1
    assert completed[0].name == "filesystem.read"


def test_reduce_is_deterministic():
    a = _final_state("tool_complete")
    b = _final_state("tool_complete")
    assert a.to_dict() == b.to_dict()
