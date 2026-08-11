"""Headless single-run execution: ``personal-ai exec`` (CLI v2, T14/T15).

Pipeline: send message → SSE stream → decode → normalize → reduce → render.
Nothing here touches a TTY: ``stdout`` carries only the requested result
protocol, ``stderr`` carries human progress. Exit codes are stable (see
:mod:`personal_ai_os.cli.output.exit_code`).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import TextIO

from personal_ai_os.cli.api.client import AsyncAPIClient
from personal_ai_os.cli.api.errors import APIError, TransportError
from personal_ai_os.cli.domain.events import UIEvent, UIEventType
from personal_ai_os.cli.domain.normalizer import Normalizer
from personal_ai_os.cli.domain.reducer import reduce
from personal_ai_os.cli.domain.state import AppState, initial_state
from personal_ai_os.cli.output.exit_code import ExitCode, exit_code_for_run_status
from personal_ai_os.cli.output.json_output import run_summary
from personal_ai_os.cli.output.jsonl import JSONLWriter
from personal_ai_os.cli.output.text import final_answer

StderrWriter = Callable[[str], None]


def _progress(stderr: StderrWriter, event: UIEvent, state: AppState) -> None:
    """Human progress lines for headless mode — stderr only, never stdout."""
    kind = event.type
    payload = dict(event.payload or {})
    if kind.value.startswith("tool."):
        name = payload.get("tool_name", "")
        if kind.value == "tool.requested":
            stderr(f"tool  {name} requested\n")
        elif kind.value == "tool.completed":
            stderr(f"tool  {name} done\n")
        elif kind.value == "tool.failed":
            stderr(f"tool  {name} failed: {payload.get('error', '')}\n")
    elif kind.value == "approval.required":
        stderr("approval required\n")
    elif kind.value == "run.completed":
        stderr("run completed\n")
    elif kind.value == "run.failed":
        stderr(f"run failed: {state.last_error or ''}\n")
    elif kind.value == "run.cancelled":
        stderr("run cancelled\n")


async def run_exec(
    client: AsyncAPIClient,
    *,
    prompt: str,
    session_id: str | None = None,
    output_format: str = "text",
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Execute one prompt headless; returns the process exit code."""
    if output_format not in ("text", "json", "stream-json"):
        stderr.write(f"unsupported output format: {output_format}\n")
        return ExitCode.USAGE_OR_CONFIG
    try:
        return await _run_exec_impl(
            client,
            prompt=prompt,
            session_id=session_id,
            output_format=output_format,
            stdout=stdout,
            stderr=stderr,
        )
    except (APIError, TransportError) as exc:
        stderr.write(f"error: {exc}\n")
        return _exit_code_for_error(exc)


async def _run_exec_impl(
    client: AsyncAPIClient,
    *,
    prompt: str,
    session_id: str | None,
    output_format: str,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if not session_id:
        created = await client.create_session(channel="cli")
        session_id = created.id
        stderr.write(f"[new session {session_id}]\n")

    send = await client.send_message(session_id, prompt)
    run_id = send.run_id
    if run_id is None:
        stderr.write("server did not return a run id\n")
        return ExitCode.PROTOCOL_OR_SCHEMA

    state = initial_state(session_id=session_id)
    state.run_id = run_id
    normalizer = Normalizer()
    writer = JSONLWriter(write=stdout.write) if output_format == "stream-json" else None

    async for server_event in client.stream_run(run_id):
        event = normalizer.normalize(server_event, run_id=run_id)
        reduce(state, event)
        # A live stream that never delivered text.delta (e.g. non-streaming
        # provider) ends with an empty answer; fetch the run record so the
        # terminal JSONL line / text output carry the real final response.
        if event.type == UIEventType.RUN_COMPLETED and not final_answer(state):
            try:
                run = await client.get_run(run_id)
                if run.final_response:
                    state.final_response = run.final_response
            except (APIError, TransportError):
                pass
        if writer is not None:
            writer.emit(event, state)
        else:
            _progress(stderr.write, event, state)

    # Fallback: a completed stream may omit the answer text (tool-only path);
    # fetch the run record for the final response.
    if state.run_status == "completed" and not final_answer(state):
        try:
            run = await client.get_run(run_id)
            if run.final_response:
                state.final_response = run.final_response
        except (APIError, TransportError):
            pass

    if output_format == "text":
        if state.run_status == "completed":
            stdout.write(final_answer(state))
            if final_answer(state) and not final_answer(state).endswith("\n"):
                stdout.write("\n")
        elif state.run_status == "waiting_approval":
            stderr.write(
                "run is waiting for approval — use `personal-ai approvals list`\n"
            )
        elif state.last_error:
            stderr.write(f"run {state.run_status}: {state.last_error}\n")
    elif output_format == "json":
        summary = run_summary(state)
        stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    return int(exit_code_for_run_status(state.run_status))


def _exit_code_for_error(exc: APIError | TransportError) -> int:
    if isinstance(exc, TransportError):
        return ExitCode.NETWORK_UNAVAILABLE
    if exc.status_code == 401:
        return ExitCode.AUTH
    if exc.status_code == 404:
        return ExitCode.PROTOCOL_OR_SCHEMA
    if exc.status_code == 429:
        return ExitCode.NETWORK_UNAVAILABLE
    if exc.status_code >= 500:
        return ExitCode.NETWORK_UNAVAILABLE
    return ExitCode.INTERNAL
