"""T54 — stress fixtures: reducer must not lose order or crash at scale."""

from __future__ import annotations

from personal_ai_os.cli.domain.events import UIEventType, ui_event
from personal_ai_os.cli.domain.reducer import reduce
from personal_ai_os.cli.domain.state import AppState, initial_state


def test_reducer_handles_10000_deltas_in_order():
    state = initial_state(session_id="s1")
    for i in range(10_000):
        reduce(
            state,
            ui_event(UIEventType.ASSISTANT_DELTA, {"text": f"{i};"}, run_id="r1"),
        )
    assert state.assistant_buffer == "".join(f"{i};" for i in range(10_000))
    assert len(state.transcript) == 1  # deltas coalesce into one streaming cell


def test_reducer_handles_1000_tool_events():
    state: AppState = initial_state()
    for i in range(1000):
        reduce(
            state,
            ui_event(
                UIEventType.TOOL_REQUESTED,
                {"tool_call_id": f"c{i}", "tool_name": f"tool.{i}"},
                run_id="r1",
            ),
        )
    assert len(state.tool_calls) == 1000
    assert state.tool_calls["c999"].name == "tool.999"


def test_transcript_trim_bounds_memory():
    state = initial_state()
    for i in range(5000):
        reduce(state, ui_event(UIEventType.ASSISTANT_DELTA, {"text": "x"}, run_id="r1"))
    # transcript stays bounded in length for rendering
    from personal_ai_os.cli.tui.render import transcript_lines

    lines = transcript_lines(state, max_cells=500)
    assert len(lines) <= 500
