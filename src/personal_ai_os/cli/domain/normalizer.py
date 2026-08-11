"""SSE → UIEvent normalization (CLI v2, T12).

Turns raw :class:`~personal_ai_os.cli.api.sse.ServerEvent` payloads into
stable :class:`~personal_ai_os.cli.domain.events.UIEvent` values, absorbing the
live/replay asymmetry of the server and suppressing raw chain-of-thought by
default (``thinking.delta`` normalizes to ``progress`` with ``raw_thinking``
flagged, never to an assistant answer).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from personal_ai_os.cli.api.sse import ServerEvent
from personal_ai_os.cli.domain.events import UIEvent, UIEventType, ui_event

# Replay emits raw LangGraph node names as event names (live does not).
_REPLAY_NODE_EVENTS = {
    "intake": "Ingesting input",
    "build_context": "Building context",
    "plan": "Planning",
    "tool_request": "Requesting tool",
    "approval": "Waiting for approval",
    "execute": "Executing tool",
    "observe": "Observing result",
    "reflect": "Reflecting",
    "memory_commit": "Committing memory",
}

# Extra names that appear in fixtures/tests but carry no UI semantics by default.
_IGNORED_EVENTS = {"heartbeat"}


class Normalizer:
    """Stateless mapper from ServerEvent to UIEvent."""

    def __init__(self, *, show_reasoning: bool = False) -> None:
        self.show_reasoning = show_reasoning

    def normalize(
        self,
        server_event: ServerEvent,
        *,
        run_id: str | None = None,
        ts: datetime | None = None,
        seq: int | None = None,
    ) -> UIEvent:
        if server_event.malformed:
            return ui_event(
                UIEventType.WARNING,
                {"message": "Malformed SSE payload dropped", "raw": server_event.raw_data},
                run_id=run_id,
                ts=ts,
                seq=seq,
            )
        name = server_event.event
        payload = dict(server_event.data or {})
        if run_id is None:
            run_id = _coerce_run_id(payload)

        handler = getattr(self, f"_on_{name.replace('.', '_')}", None)
        if handler is not None:
            return handler(payload, run_id=run_id, ts=ts, seq=seq)

        if name in _REPLAY_NODE_EVENTS:
            return ui_event(
                UIEventType.PROGRESS,
                {"message": _REPLAY_NODE_EVENTS[name], "step": name, **payload},
                run_id=run_id,
                ts=ts,
                seq=seq,
            )
        if name in _IGNORED_EVENTS:
            return ui_event(UIEventType.UNKNOWN, {"message": f"ignored: {name}"}, run_id=run_id)
        # Unknown server event: surface as a diagnostic notice, never a crash.
        return ui_event(
            UIEventType.UNKNOWN,
            {"message": f"Unrecognized server event `{name}`", "event": name, **payload},
            run_id=run_id,
            ts=ts,
            seq=seq,
        )

    # -- handlers ----------------------------------------------------------

    def _on_run_started(self, payload, *, run_id, ts, seq) -> UIEvent:
        return ui_event(
            UIEventType.RUN_STARTED, {"status": payload.get("status", "running")},
            run_id=run_id, ts=ts, seq=seq,
        )

    def _on_thinking_delta(self, payload, *, run_id, ts, seq) -> UIEvent:
        text = payload.get("text", "")
        if self.show_reasoning:
            message = text
        else:
            message = ""
        return ui_event(
            UIEventType.PROGRESS,
            {"message": message, "raw_thinking": True, "shown": self.show_reasoning},
            run_id=run_id, ts=ts, seq=seq,
        )

    def _on_text_delta(self, payload, *, run_id, ts, seq) -> UIEvent:
        # Replay emits `decide` steps as text.delta carrying step metadata, not
        # assistant content.
        if "step_type" in payload:
            return ui_event(
                UIEventType.PROGRESS,
                {"message": "Decision step", "step": "decide", **payload},
                run_id=run_id, ts=ts, seq=seq,
            )
        return ui_event(
            UIEventType.ASSISTANT_DELTA,
            {"text": payload.get("text", ""), "replay": payload.get("replay", False)},
            run_id=run_id, ts=ts, seq=seq,
        )

    def _on_tool_requested(self, payload, *, run_id, ts, seq) -> UIEvent:
        tool_call = payload.get("tool_call") or {}
        if isinstance(tool_call, str):
            try:
                tool_call = json.loads(tool_call)
            except json.JSONDecodeError:
                tool_call = {}
        if not isinstance(tool_call, dict):
            tool_call = {}
        function = tool_call.get("function") or {}
        name = function.get("name") or payload.get("tool_name", "")
        return ui_event(
            UIEventType.TOOL_REQUESTED,
            {
                "tool_call_id": tool_call.get("id"),
                "tool_name": name,
                "arguments": _parse_arguments(function.get("arguments")),
            },
            run_id=run_id, ts=ts, seq=seq,
        )

    def _on_tool_started(self, payload, *, run_id, ts, seq) -> UIEvent:
        return self._tool_event(UIEventType.TOOL_STARTED, payload, run_id, ts, seq)

    def _on_tool_completed(self, payload, *, run_id, ts, seq) -> UIEvent:
        return self._tool_event(UIEventType.TOOL_COMPLETED, payload, run_id, ts, seq)

    def _on_tool_failed(self, payload, *, run_id, ts, seq) -> UIEvent:
        return self._tool_event(UIEventType.TOOL_FAILED, payload, run_id, ts, seq)

    def _tool_event(self, type_, payload, run_id, ts, seq) -> UIEvent:
        tool_id = (
            payload.get("tool_call_id")
            or payload.get("tool_call")
            or payload.get("id")
        )
        return ui_event(
            type_,
            {
                "tool_call_id": str(tool_id) if tool_id else None,
                "tool_name": payload.get("tool_name") or payload.get("tool", ""),
                "success": payload.get("success"),
                "error": payload.get("error") or payload.get("error_code"),
                "latency_ms": payload.get("latency_ms") or payload.get("duration_ms"),
                "summary": payload.get("summary", ""),
            },
            run_id=run_id, ts=ts, seq=seq,
        )

    def _on_approval_required(self, payload, *, run_id, ts, seq) -> UIEvent:
        return ui_event(
            UIEventType.APPROVAL_REQUIRED,
            {"status": payload.get("status", "waiting_approval")},
            run_id=run_id, ts=ts, seq=seq,
        )

    def _on_run_completed(self, payload, *, run_id, ts, seq) -> UIEvent:
        return ui_event(UIEventType.RUN_COMPLETED, payload, run_id=run_id, ts=ts, seq=seq)

    def _on_run_failed(self, payload, *, run_id, ts, seq) -> UIEvent:
        return ui_event(UIEventType.RUN_FAILED, payload, run_id=run_id, ts=ts, seq=seq)

    def _on_run_cancelled(self, payload, *, run_id, ts, seq) -> UIEvent:
        return ui_event(UIEventType.RUN_CANCELLED, payload, run_id=run_id, ts=ts, seq=seq)


def _coerce_run_id(payload: Mapping[str, Any]) -> str | None:
    for key in ("run_id", "run", "id"):
        if payload.get(key):
            return str(payload[key])
    return None


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"raw": raw}
    return {}


def utcnow() -> datetime:
    return datetime.now(UTC)
