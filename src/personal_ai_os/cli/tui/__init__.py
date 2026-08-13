"""TUI entry: Textual app with a plain/accessible fallback (CLI v2, T21/T26).

``run_app`` selects the presentation by environment: Textual when stdout is a
TTY and color is allowed, otherwise a plain input loop that renders the same
presentation state as plain lines (screen-reader / pipe / NO_COLOR friendly).
"""

from __future__ import annotations

import asyncio
import sys

from personal_ai_os.cli.api.client import AsyncAPIClient
from personal_ai_os.cli.domain.state import AppState
from personal_ai_os.cli.tui.capabilities import color_enabled, is_tty


class _PlainSink:
    """Stand-in app object for the plain loop.

    Progress is rendered by the loop itself (plain lines to stdout/stderr), so
    the controller's ``refresh_ui`` hook is intentionally a no-op here.
    """

    def __init__(self) -> None:
        self.state = None

    def refresh_ui(self) -> None:
        pass


def plain_transcript_lines(state: AppState, cursor: int) -> tuple[list[str], int]:
    """Render only transcript cells added after ``cursor`` using safe renderers."""
    from personal_ai_os.cli.tui.render import cell_lines

    start = min(max(cursor, 0), len(state.transcript))
    lines: list[str] = []
    for cell in state.transcript[start:]:
        lines.extend(cell_lines(cell))
    return lines, len(state.transcript)


async def run_plain(client: AsyncAPIClient, *, session_id: str | None = None) -> None:
    """Non-TTY interactive loop: plain transcript lines + ``input()``."""
    from personal_ai_os.cli.tui.controllers.chat import ChatController

    sink = _PlainSink()
    controller = ChatController(client, sink, session_id=session_id)
    await controller.restore_session()
    sink.state = controller.state
    restored_lines, transcript_cursor = plain_transcript_lines(controller.state, 0)
    for line in restored_lines:
        print(line)
    while controller.busy:
        await asyncio.sleep(0.05)
    resumed_lines, transcript_cursor = plain_transcript_lines(
        controller.state, transcript_cursor
    )
    for line in resumed_lines:
        print(line)
    print("Personal AI — plain mode (type Ctrl+C to exit).", file=sys.stderr)
    while True:
        try:
            prompt = await asyncio.to_thread(_read_prompt)
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            break
        if not prompt.strip():
            continue
        if prompt.strip().startswith("/"):
            print(f"/ command not supported in plain mode: {prompt.strip()}", file=sys.stderr)
            continue
        await controller.send(prompt.strip())
        while controller.busy:
            await asyncio.sleep(0.05)
        lines, transcript_cursor = plain_transcript_lines(
            controller.state, transcript_cursor
        )
        for line in lines:
            print(line)
        if controller.state.run_status == "waiting_approval":
            print("! approval required — use `personal-ai approvals list`", file=sys.stderr)


def _read_prompt() -> str:
    return input("> ")


def run_app(
    client: AsyncAPIClient | None = None,
    *,
    session_id: str | None = None,
    force_plain: bool = False,
) -> None:
    """Run the interactive UI, choosing Textual or plain by capability."""
    from personal_ai_os.cli.bootstrap import build_client

    owns_client = client is None
    if client is None:
        client = build_client()

    try:
        if force_plain or not (is_tty(sys.stdout) and color_enabled(sys.stdout)):
            asyncio.run(run_plain(client, session_id=session_id))
        else:
            from personal_ai_os.cli.tui.app import PersonalAIApp

            PersonalAIApp(client=client, session_id=session_id).run()
    finally:
        if owns_client:
            asyncio.run(client.aclose())
