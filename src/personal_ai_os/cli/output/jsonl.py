"""Versioned JSONL stream writer (CLI v2, T14).

``exec -o stream-json`` writes one self-contained JSON object per line to
stdout. The schema (``v=1``) is the client's PUBLIC contract — it is not a raw
passthrough of server SSE payloads.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from personal_ai_os.cli.domain.events import UIEvent, UIEventType
from personal_ai_os.cli.domain.state import AppState
from personal_ai_os.cli.sanitize import strip_control_sequences

_SCHEMA_VERSION = 1

Writer = Callable[[str], None]


def _clean(value) -> Any:
    return strip_control_sequences(value) if isinstance(value, str) else value


def public_line(event: UIEvent, state: AppState) -> dict[str, Any] | None:
    """Map a UIEvent to one public JSONL line (or ``None`` to omit)."""
    run_id = event.run_id or state.run_id
    payload = dict(event.payload or {})
    kind = event.type

    if kind == UIEventType.RUN_STARTED:
        return {"v": _SCHEMA_VERSION, "type": "run.started", "run_id": run_id,
                "status": payload.get("status", "running")}
    if kind == UIEventType.PROGRESS:
        if payload.get("raw_thinking") and not payload.get("shown"):
            return None  # suppressed thinking never enters the public stream
        return {"v": _SCHEMA_VERSION, "type": "progress", "run_id": run_id,
                "message": _clean(payload.get("message", ""))}
    if kind == UIEventType.ASSISTANT_DELTA:
        return {"v": _SCHEMA_VERSION, "type": "assistant.delta", "run_id": run_id,
                "text": _clean(payload.get("text", ""))}
    if kind == UIEventType.TOOL_REQUESTED:
        return {"v": _SCHEMA_VERSION, "type": "tool.requested", "run_id": run_id,
                "tool": {"name": _clean(payload.get("tool_name", "")), "id": payload.get("tool_call_id")}}
    if kind == UIEventType.TOOL_STARTED:
        return {"v": _SCHEMA_VERSION, "type": "tool.started", "run_id": run_id,
                "tool": {"name": _clean(payload.get("tool_name", "")), "id": payload.get("tool_call_id")}}
    if kind == UIEventType.TOOL_COMPLETED:
        return {"v": _SCHEMA_VERSION, "type": "tool.completed", "run_id": run_id,
                "tool": {"name": payload.get("tool_name", ""), "id": payload.get("tool_call_id"),
                         "latency_ms": payload.get("latency_ms")}}
    if kind == UIEventType.TOOL_FAILED:
        return {"v": _SCHEMA_VERSION, "type": "tool.failed", "run_id": run_id,
                "tool": {"name": payload.get("tool_name", ""), "id": payload.get("tool_call_id"),
                         "error": _clean(payload.get("error"))}}
    if kind == UIEventType.APPROVAL_REQUIRED:
        return {"v": _SCHEMA_VERSION, "type": "approval.required", "run_id": run_id}
    if kind == UIEventType.RUN_COMPLETED:
        return {"v": _SCHEMA_VERSION, "type": "run.completed", "run_id": run_id,
                "status": "completed", "result": {"text": _clean(state.final_response)}}
    if kind == UIEventType.RUN_FAILED:
        return {"v": _SCHEMA_VERSION, "type": "run.failed", "run_id": run_id,
                "status": "failed", "error": {"message": _clean(state.last_error)}}
    if kind == UIEventType.RUN_CANCELLED:
        return {"v": _SCHEMA_VERSION, "type": "run.cancelled", "run_id": run_id,
                "status": "cancelled"}
    if kind == UIEventType.WARNING:
        return {"v": _SCHEMA_VERSION, "type": "warning", "run_id": run_id,
                "message": payload.get("message", "warning")}
    if kind == UIEventType.ERROR:
        return {"v": _SCHEMA_VERSION, "type": "error", "run_id": run_id,
                "message": _clean(payload.get("message", state.last_error))}
    return None


class JSONLWriter:
    """Write public JSONL lines to a text stream (default stdout)."""

    def __init__(self, write: Writer = sys.stdout.write) -> None:
        self.write = write

    def emit(self, event: UIEvent, state: AppState) -> None:
        line = public_line(event, state)
        if line is not None:
            self.write(json.dumps(line, ensure_ascii=False) + "\n")
