"""Pure-ish reducer: UIEvent stream → AppState (CLI v2, T13).

``reduce`` mutates and returns the passed :class:`AppState`. It performs no
I/O, so tests can replay fixture events and assert a deterministic final state.
"""

from __future__ import annotations

from datetime import UTC, datetime

from personal_ai_os.cli.domain.events import UIEvent, UIEventType
from personal_ai_os.cli.domain.state import (
    CELL_APPROVAL,
    CELL_ASSISTANT,
    CELL_ERROR,
    CELL_NOTICE,
    CELL_PROGRESS,
    CELL_RUN_STATUS,
    CELL_TOOL,
    CELL_WARNING,
    AppState,
    ConnectionState,
    ToolViewState,
    TranscriptCell,
)


def reduce(state: AppState, event: UIEvent) -> AppState:
    """Apply one UIEvent to the state (in place) and return it."""
    kind = event.type
    payload = dict(event.payload or {})
    now = event.ts or _now()

    if kind == UIEventType.CONNECTION_OPENED:
        state.connection_state = ConnectionState.CONNECTED
        return state
    if kind == UIEventType.CONNECTION_CLOSED:
        state.connection_state = ConnectionState.CLOSED
        return state

    if kind == UIEventType.RUN_STARTED:
        state.run_id = event.run_id or state.run_id
        state.run_status = payload.get("status") or "running"
        state.connection_state = ConnectionState.CONNECTED
        _add(state, CELL_RUN_STATUS, now, {"status": state.run_status}, f"Run {state.run_id}")
        return state

    if kind == UIEventType.PROGRESS:
        if payload.get("raw_thinking") and not payload.get("shown"):
            return state  # suppressed chain-of-thought: keep out of transcript
        text = payload.get("message", "")
        if text:
            _add(state, CELL_PROGRESS, now, payload, text)
        return state

    if kind == UIEventType.ASSISTANT_DELTA:
        text = payload.get("text", "")
        state.assistant_buffer += text
        last = state.last_cell
        if last is not None and last.kind == CELL_ASSISTANT and last.payload.get("streaming"):
            last.text += text
        else:
            _add(state, CELL_ASSISTANT, now, {"streaming": True}, text)
        return state

    if kind == UIEventType.ASSISTANT_COMPLETED:
        if state.last_cell and state.last_cell.kind == CELL_ASSISTANT:
            state.last_cell.payload["streaming"] = False
        return state

    if kind in (UIEventType.TOOL_REQUESTED, UIEventType.TOOL_STARTED):
        tool = _upsert_tool(state, event, now)
        tool.status = "requested" if kind == UIEventType.TOOL_REQUESTED else "running"
        tool.started_at = tool.started_at or now.isoformat()
        return state

    if kind == UIEventType.TOOL_COMPLETED:
        tool = _upsert_tool(state, event, now)
        tool.status = "completed"
        tool.latency_ms = _to_int(payload.get("latency_ms"))
        tool.summary = str(payload.get("summary", tool.summary))
        tool.result_preview = str(payload.get("result_preview", tool.result_preview))
        return state

    if kind == UIEventType.TOOL_FAILED:
        tool = _upsert_tool(state, event, now)
        tool.status = "failed"
        tool.error = str(payload.get("error") or payload.get("error_code") or "failed")
        tool.latency_ms = _to_int(payload.get("latency_ms"))
        return state

    if kind == UIEventType.APPROVAL_REQUIRED:
        state.run_status = "waiting_approval"
        state.pending_approval = dict(payload)
        state.connection_state = ConnectionState.CLOSED
        _add(
            state,
            CELL_APPROVAL,
            now,
            dict(payload),
            f"Approval required · run {state.run_id}",
        )
        return state

    if kind == UIEventType.RUN_COMPLETED:
        state.run_status = "completed"
        state.final_response = state.assistant_buffer or state.final_response
        _flush_streaming_assistant(state)
        state.connection_state = ConnectionState.CLOSED
        _add(state, CELL_RUN_STATUS, now, {"status": "completed"}, "Run completed")
        return state

    if kind == UIEventType.RUN_FAILED:
        state.run_status = "failed"
        state.last_error = str(
            payload.get("error")
            or payload.get("message")
            or (payload.get("status") or "failed")
        )
        state.connection_state = ConnectionState.CLOSED
        _add(state, CELL_RUN_STATUS, now, {"status": "failed"}, f"Run failed: {state.last_error}")
        return state

    if kind == UIEventType.RUN_CANCELLED:
        state.run_status = "cancelled"
        state.connection_state = ConnectionState.CLOSED
        _add(state, CELL_RUN_STATUS, now, {"status": "cancelled"}, "Run cancelled")
        return state

    if kind == UIEventType.WARNING:
        message = payload.get("message", "warning")
        state.warnings.append(message)
        _add(state, CELL_WARNING, now, payload, message)
        return state

    if kind == UIEventType.ERROR:
        message = payload.get("message", payload.get("detail", "error"))
        state.last_error = str(message)
        _add(state, CELL_ERROR, now, payload, str(message))
        return state

    if kind == UIEventType.UNKNOWN:
        _add(state, CELL_NOTICE, now, payload, payload.get("message", ""))
        return state

    return state


def _upsert_tool(state: AppState, event: UIEvent, now: datetime) -> ToolViewState:
    payload = dict(event.payload or {})
    tool_id = payload.get("tool_call_id") or payload.get("id") or payload.get("tool_call")
    tool_id = str(tool_id) if tool_id else f"tool-{len(state.tool_calls)}"
    tool = state.tool_calls.get(tool_id)
    if tool is None:
        tool = ToolViewState(
            id=tool_id,
            name=str(payload.get("tool_name", "")),
            status="requested",
            risk_level=_to_int(payload.get("risk_level")),
            arguments_preview=dict(payload.get("arguments_preview") or payload.get("arguments") or {}),
            started_at=now.isoformat(),
        )
        state.tool_calls[tool_id] = tool
        _add(
            state,
            CELL_TOOL,
            now,
            {"tool_call_id": tool_id, "tool_name": tool.name, "status": tool.status},
            f"{tool.name} {tool.status}",
        )
    elif event.type == UIEventType.TOOL_REQUESTED:
        if not tool.name:
            tool.name = str(payload.get("tool_name", ""))
        if not tool.arguments_preview:
            tool.arguments_preview = dict(
                payload.get("arguments_preview") or payload.get("arguments") or {}
            )
        _add(
            state,
            CELL_TOOL,
            now,
            {"tool_call_id": tool_id, "tool_name": tool.name, "status": tool.status},
            f"{tool.name} requested",
        )
    return tool


def _flush_streaming_assistant(state: AppState) -> None:
    if state.last_cell and state.last_cell.kind == CELL_ASSISTANT:
        state.last_cell.payload["streaming"] = False


def _add(
    state: AppState,
    kind: str,
    ts: datetime,
    payload: dict,
    text: str,
) -> None:
    state.transcript.append(
        TranscriptCell(kind=kind, ts=ts, payload=payload, text=text)
    )


def _to_int(value) -> int | None:  # noqa: ANN001
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    return datetime.now(UTC)
