"""SSE fixture loader (CLI v2 Phase 0, T03).

Each ``*.sse`` fixture in :mod:`tests.unit.cli.fixtures` is a raw
``text/event-stream`` body exactly as the server emits it (sse-starlette
framing: ``event:`` + ``data:`` lines, ``\\n\\n`` separators, optional
``: comment`` keep-alive lines). The v2 SSE decoder (Phase 1, T11) must pass
every fixture here.

Semantics of each fixture (see also the module docstring per file):

* ``simple_complete``   — minimal completed run, two text deltas.
* ``thinking_and_text`` — thinking delta streamed before the answer.
* ``tool_complete``     — live-style ``tool.requested`` with a raw LLM tool_call.
* ``tool_failed``       — tool failure surfaced between tool.requested and answer.
* ``approval_pause``    — stream ends on ``approval.required`` (no terminal status).
* ``multiline_data``    — ``data:`` split across lines; must be joined with ``\\n``.
* ``comments_heartbeat``— server keep-alive ``: ping`` comments to skip.
* ``unexpected_eof``    — stream ends without a terminal event (connection drop).
* ``unknown_event``     — an event name the client has never seen; must not crash.
* ``replay_run``        — DB replay shape: ``replay:true`` deltas + raw node-name steps.
* ``duplicate_event``   — same terminal event delivered twice (reconnect/dup).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_ALL_FIXTURES = (
    "simple_complete",
    "thinking_and_text",
    "tool_complete",
    "tool_failed",
    "approval_pause",
    "multiline_data",
    "comments_heartbeat",
    "unexpected_eof",
    "unknown_event",
    "replay_run",
    "duplicate_event",
)


def load_raw(name: str) -> str:
    """Return the raw SSE body of a named fixture."""
    return (FIXTURES_DIR / f"{name}.sse").read_text(encoding="utf-8")


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Minimal reference SSE parser for tests.

    Returns ``[(event, data_payload), ...]``. This is intentionally simple and
    independent of the production decoder so fixtures remain a trustworthy
    oracle; it handles single-line and multi-line ``data:`` and skips comments.
    """
    events: list[tuple[str, dict[str, Any]]] = []
    event: str | None = None
    data_lines: list[str] = []
    for raw_line in body.splitlines():
        if not raw_line.strip():
            _flush(event, data_lines, events)
            event, data_lines = None, []
            continue
        if raw_line.startswith(":"):
            continue
        if raw_line.startswith("event:"):
            event = raw_line[len("event:"):].strip()
            continue
        if raw_line.startswith("data:"):
            data_lines.append(raw_line[len("data:"):].lstrip())
            continue
    _flush(event, data_lines, events)
    return events


def _flush(
    event: str | None,
    data_lines: list[str],
    out: list[tuple[str, dict[str, Any]]],
) -> None:
    if event is None or not data_lines:
        return
    payload = json.loads("\n".join(data_lines))
    out.append((event, payload))
