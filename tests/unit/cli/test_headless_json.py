"""T14 — headless JSON output contract tests."""

from __future__ import annotations

from personal_ai_os.cli.output.json_output import run_summary
from tests.unit.cli import sse_fixtures


def test_json_summary_completed():
    state = sse_fixtures.reduce_fixture("simple_complete")
    summary = run_summary(state)
    assert summary["v"] == 1
    assert summary["status"] == "completed"
    assert summary["run_id"] == "r1"
    assert summary["output"] == {"text": "Hello world"}
    assert summary["error"] is None


def test_json_summary_waiting_approval_has_no_output():
    state = sse_fixtures.reduce_fixture("approval_pause")
    summary = run_summary(state)
    assert summary["status"] == "waiting_approval"
    assert summary["output"] is None


def test_json_summary_failed_has_error():
    state = sse_fixtures.reduce_fixture("unexpected_eof")
    # no terminal event: status stays running, no error recorded yet
    assert state.run_status == "running"
    # simulate a failed terminal
    from personal_ai_os.cli.domain.events import UIEventType, ui_event
    from personal_ai_os.cli.domain.reducer import reduce

    reduce(state, ui_event(UIEventType.RUN_FAILED, {"status": "failed"}, run_id="r1"))
    summary = run_summary(state)
    assert summary["status"] == "failed"
    assert summary["error"] == {"message": "failed"}
