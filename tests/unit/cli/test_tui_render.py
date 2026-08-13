"""T23/T25 — TUI pure render functions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from rich.cells import cell_len

from personal_ai_os.cli.domain.state import TranscriptCell, initial_state
from personal_ai_os.cli.tui import plain_transcript_lines
from personal_ai_os.cli.tui.app import context_summary_lines, memory_detail_lines
from personal_ai_os.cli.tui.command_registry import lookup
from personal_ai_os.cli.tui.render import status_bar_text, status_glyph, transcript_lines
from tests.unit.cli import sse_fixtures


def test_status_bar_shows_session_run_and_status():
    state = sse_fixtures.reduce_fixture("simple_complete")
    line = status_bar_text(state)
    assert state.run_id in line
    assert "completed" in line


def test_status_bar_approval_state():
    state = sse_fixtures.reduce_fixture("approval_pause")
    assert "waiting_approval" in status_bar_text(state)


@pytest.mark.parametrize("width", [60, 80, 120, 160])
def test_status_bar_width_snapshots(width):
    state = initial_state(session_id="会话-session")
    state.run_id = "运行-run"
    state.run_status = "waiting_approval"

    snapshot = status_bar_text(state, width=width)

    assert cell_len(snapshot) == width
    assert snapshot.rstrip().startswith("session 会话-sess")


def test_status_glyphs_ascii():
    assert status_glyph("completed") == "OK"
    assert status_glyph("failed") == "ERR"
    assert status_glyph("waiting_approval") == "!"
    assert status_glyph("running") == "*"
    assert status_glyph("nope") == "?"


def test_transcript_lines_include_tool_and_answer():
    state = sse_fixtures.reduce_fixture("tool_complete")
    lines = transcript_lines(state)
    assert any("filesystem.read" in line for line in lines)
    assert any("Found it" in line for line in lines)


def test_transcript_lines_max_cells_trims():
    state = sse_fixtures.reduce_fixture("tool_complete")
    full = len(transcript_lines(state))
    assert len(transcript_lines(state, max_cells=1)) <= full


def test_transcript_lines_never_crash_on_unknown_kind():
    state = sse_fixtures.reduce_fixture("unknown_event")
    assert isinstance(transcript_lines(state), list)


def test_plain_transcript_sanitizes_and_only_emits_new_cells():
    state = initial_state()
    state.transcript.extend(
        [
            TranscriptCell(kind="assistant", text="first\x1b]0;owned\x07"),
            TranscriptCell(kind="assistant", text="second"),
        ]
    )

    first, cursor = plain_transcript_lines(state, 0)
    second, next_cursor = plain_transcript_lines(state, cursor)

    assert first == ["first", "second"]
    assert second == []
    assert cursor == next_cursor == 2


def test_context_command_has_own_secret_safe_summary():
    state = initial_state(session_id="s1")
    state.run_id = "r1"
    run = SimpleNamespace(
        state={
            "_cached_context": {"system_prompt": "SECRET SYSTEM PROMPT"},
            "conversation_summary": "private summary",
            "context_items": [{"type": "memory", "content": "private memory"}],
            "messages": [{"role": "user", "content": "private turn"}],
            "tool_results": [],
        },
        model_usage={"total_tokens": 42},
    )

    lines = context_summary_lines(state, run)

    assert lookup("context").action == "cmd_context"
    assert "Memories used        : 1" in lines
    assert "Model tokens         : 42" in lines
    assert "SECRET SYSTEM PROMPT" not in str(lines)
    assert "private memory" not in str(lines)
    assert "private turn" not in str(lines)


def test_memory_detail_displays_provenance():
    memory = SimpleNamespace(
        id="memory-1",
        type="fact",
        scope="global",
        source_type="conversation",
        source_id="run-1",
        sensitivity="personal",
        confidence=0.9,
        summary="summary",
        content="content",
    )

    assert "Source    : conversation:run-1" in memory_detail_lines(memory)
