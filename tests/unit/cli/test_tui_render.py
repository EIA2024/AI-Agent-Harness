"""T23/T25 — TUI pure render functions."""

from __future__ import annotations

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
