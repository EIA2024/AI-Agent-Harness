"""T14 — headless text renderer + exit-code mapping tests."""

from __future__ import annotations

from personal_ai_os.cli.output.exit_code import ExitCode, exit_code_for_run_status
from personal_ai_os.cli.output.text import final_answer, render_transcript
from tests.unit.cli import sse_fixtures


def _state(name: str):
    return sse_fixtures.reduce_fixture(name)


def test_text_answer_for_completed():
    state = _state("simple_complete")
    assert final_answer(state) == "Hello world"


def test_text_answer_empty_until_terminal():
    state = _state("unexpected_eof")
    # no terminal event → final_response not committed, but buffer retained
    assert state.final_response == ""
    assert state.assistant_buffer == "partial answer, connection dropped"


def test_render_transcript_contains_answer_and_tool():
    state = _state("tool_complete")
    rendered = render_transcript(state)
    assert "Found it" in rendered
    assert "filesystem.read" in rendered


def test_exit_code_mapping():
    assert exit_code_for_run_status("completed") == ExitCode.OK
    assert exit_code_for_run_status("waiting_approval") == ExitCode.APPROVAL_REQUIRED
    assert exit_code_for_run_status("failed") == ExitCode.RUN_FAILED
    assert exit_code_for_run_status("cancelled") == ExitCode.CANCELLED
    assert ExitCode.USAGE_OR_CONFIG == 2
    assert ExitCode.AUTH == 3
    assert ExitCode.PROTOCOL_OR_SCHEMA == 8
