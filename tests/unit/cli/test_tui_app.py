"""T21/T24 — Textual app shell integration tests (run_test, no real terminal)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from personal_ai_os.cli.api.sse import SSEDecoder
from personal_ai_os.cli.tui.app import PersonalAIApp
from personal_ai_os.cli.tui.widgets.composer import Composer
from personal_ai_os.cli.tui.widgets.transcript import Transcript
from tests.unit.cli import sse_fixtures


class FakeClient:
    """Minimal AsyncAPIClient stand-in that replays a fixture's SSE body."""

    def __init__(self, sse_body: str, *, hang: bool = False) -> None:
        self.sse_body = sse_body
        self.hang = hang
        self.sent: list[str] = []
        self.cancelled: str | None = None
        self.created = False

    async def create_session(self, channel: str = "cli", **extra):
        self.created = True
        return SimpleNamespace(id="s1", channel=channel)

    async def send_message(self, session_id: str, text: str):
        self.sent.append(text)
        return SimpleNamespace(run_id="r1", status="running", message_id="m1")

    async def stream_run(self, run_id: str):
        if self.hang:
            await asyncio.Event().wait()  # never ends until cancelled
        decoder = SSEDecoder()
        for line in self.sse_body.splitlines(keepends=False):
            for ev in decoder.feed_line(line):
                yield ev
        for ev in decoder.finish():
            yield ev

    async def cancel_run(self, run_id: str):
        self.cancelled = run_id
        return SimpleNamespace(id=run_id, status="cancelled")

    async def aclose(self) -> None:
        pass


async def _wait_idle(app: PersonalAIApp, pilot) -> None:  # noqa: ANN001
    for _ in range(200):
        if app.controller is not None and not app.controller.busy:
            break
        await pilot.pause()
        await asyncio.sleep(0.01)


async def test_app_mounts_and_shows_status():
    app = PersonalAIApp(client=FakeClient(sse_fixtures.load_raw("simple_complete")))
    async with app.run_test() as pilot:
        assert app.controller is not None
        assert app.query_one("#status") is not None
        assert app.query_one("#transcript", Transcript) is not None
        assert app.query_one("#composer", Composer) is not None


async def test_submit_streams_answer_into_transcript():
    client = FakeClient(sse_fixtures.load_raw("simple_complete"))
    app = PersonalAIApp(client=client)
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.text = "hello"
        await pilot.press("enter")
        await _wait_idle(app, pilot)
        rendered = str(app.query_one("#transcript", Transcript).render())
        assert "Hello world" in rendered
        assert client.sent == ["hello"]
        assert client.created is True  # a session was auto-created


async def test_composer_re_enabled_after_run_completes():
    """Bugfix: after one run the composer must be usable again (not disabled)."""
    client = FakeClient(sse_fixtures.load_raw("simple_complete"))
    app = PersonalAIApp(client=client)
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.text = "hello"
        await pilot.press("enter")
        await _wait_idle(app, pilot)
        assert app.controller is not None and not app.controller.busy
        assert composer.disabled is False
        # and the composer must accept input for a second turn
        composer.text = "second"
        await pilot.press("enter")
        await _wait_idle(app, pilot)
        assert client.sent == ["hello", "second"]


async def test_tool_event_renders():
    client = FakeClient(sse_fixtures.load_raw("tool_complete"))
    app = PersonalAIApp(client=client)
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.text = "read src"
        await pilot.press("enter")
        await _wait_idle(app, pilot)
        rendered = str(app.query_one("#transcript", Transcript).render())
        assert "filesystem.read" in rendered
        assert "Found it" in rendered


async def test_ctrl_c_cancels_active_run():
    client = FakeClient("", hang=True)
    app = PersonalAIApp(client=client)
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.text = "slow prompt"
        await pilot.press("enter")
        await pilot.pause()
        # the stream is hanging (never ends) → busy
        assert app.controller is not None and app.controller.busy
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert client.cancelled == "r1"
