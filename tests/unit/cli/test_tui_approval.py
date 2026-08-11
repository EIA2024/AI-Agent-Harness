"""T30/T31/T32 — session resume + approval flow in the TUI (run_test)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from textual.css.query import NoMatches

from personal_ai_os.cli.api.sse import SSEDecoder
from personal_ai_os.cli.tui.app import PersonalAIApp
from personal_ai_os.cli.tui.controllers.chat import ChatController
from personal_ai_os.cli.tui.screens.approval import ApprovalScreen
from personal_ai_os.cli.tui.widgets.composer import Composer
from personal_ai_os.cli.tui.widgets.transcript import Transcript
from tests.unit.cli import sse_fixtures


def _yield(body: str):
    decoder = SSEDecoder()
    for line in body.splitlines(keepends=False):
        for ev in decoder.feed_line(line):
            yield ev
    for ev in decoder.finish():
        yield ev


class ApprovalFakeClient:
    """Fake client for the approval pause → resume → complete flow."""

    def __init__(self, pause_body: str, resume_body: str) -> None:
        self.pause_body = pause_body
        self.resume_body = resume_body
        self._stream_calls = 0
        self.sent: list[str] = []
        self.approved_ids: list[str] = []
        self.rejected_ids: list[str] = []
        self.edited: list[tuple[str, dict]] = []
        self.resumed: list[tuple[str, str]] = []
        self.created = False

    async def create_session(self, channel="cli", **extra):
        self.created = True
        return SimpleNamespace(id="s1", channel=channel)

    async def send_message(self, session_id, text):
        self.sent.append(text)
        return SimpleNamespace(run_id="r1", status="running", message_id="m1")

    async def stream_run(self, run_id):
        body = self.pause_body if self._stream_calls == 0 else self.resume_body
        self._stream_calls += 1
        for event in _yield(body):
            yield event

    async def list_approvals(self, status="pending"):
        return [
            SimpleNamespace(
                id="a1", run_id="r1", tool_name="email.send",
                action_summary="send mail", risk_level=3,
                arguments_preview={"to": "boss@x.com"}, status="pending",
            )
        ]

    async def approve(self, approval_id):
        self.approved_ids.append(approval_id)
        return SimpleNamespace(id=approval_id, status="approved")

    async def reject(self, approval_id):
        self.rejected_ids.append(approval_id)
        return SimpleNamespace(id=approval_id, status="rejected")

    async def edit_approval(self, approval_id, edited_arguments):
        self.edited.append((approval_id, edited_arguments))
        return SimpleNamespace(id=approval_id, status="edited")

    async def resume_run(self, run_id, *, approval_id=None, decision=None, edited_arguments=None):
        self.resumed.append((run_id, decision))
        return SimpleNamespace(id=run_id, status="running")

    async def aclose(self):
        pass


async def _wait_for_screen(app, screen_type, pilot):  # noqa: ANN001
    for _ in range(300):
        if isinstance(app.screen, screen_type):
            try:
                app.screen.query_one("#edit-input")
                return True  # modal content is mounted
            except NoMatches:
                pass
        await pilot.pause()
        await asyncio.sleep(0.01)
    return False


async def _wait_idle(app, pilot) -> None:  # noqa: ANN001
    for _ in range(200):
        if app.controller is not None and not app.controller.busy:
            break
        await pilot.pause()
        await asyncio.sleep(0.01)


async def test_session_resume_uses_given_session():
    client = ApprovalFakeClient("", "")
    app = PersonalAIApp(client=client, session_id="existing-session")
    async with app.run_test() as pilot:
        assert app.controller is not None
        assert app.controller.state.session_id == "existing-session"
        assert client.created is False


async def test_controller_does_not_create_session_when_resuming():
    client = ApprovalFakeClient("", "")
    sink = SimpleNamespace(refresh_ui=lambda: None)
    controller = ChatController(client, sink, session_id="existing-session")
    assert controller.state.session_id == "existing-session"


async def test_approval_modal_appears_and_approve_resumes():
    client = ApprovalFakeClient(
        sse_fixtures.load_raw("approval_pause"), sse_fixtures.load_raw("simple_complete")
    )
    app = PersonalAIApp(client=client)
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.text = "send email"
        await pilot.press("enter")
        assert await _wait_for_screen(app, ApprovalScreen, pilot)
        app.screen.choose_approve()
        await pilot.pause()
        await _wait_idle(app, pilot)
        assert client.approved_ids == ["a1"]
        assert ("r1", "approved") in client.resumed
        rendered = str(app.query_one("#transcript", Transcript).render())
        assert "Hello world" in rendered


async def test_approval_modal_reject_resumes():
    client = ApprovalFakeClient(
        sse_fixtures.load_raw("approval_pause"), sse_fixtures.load_raw("simple_complete")
    )
    app = PersonalAIApp(client=client)
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.text = "send email"
        await pilot.press("enter")
        assert await _wait_for_screen(app, ApprovalScreen, pilot)
        app.screen.choose_reject()
        await pilot.pause()
        await _wait_idle(app, pilot)
        assert client.rejected_ids == ["a1"]
        assert ("r1", "rejected") in client.resumed


async def test_approval_modal_edit_sends_edited_arguments():
    client = ApprovalFakeClient(
        sse_fixtures.load_raw("approval_pause"), sse_fixtures.load_raw("simple_complete")
    )
    app = PersonalAIApp(client=client)
    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.text = "send email"
        await pilot.press("enter")
        assert await _wait_for_screen(app, ApprovalScreen, pilot)
        from textual.widgets import Input

        app.screen.query_one("#edit-input", Input).value = '{"to": "other@x.com"}'
        app.screen.choose_edit()
        await pilot.pause()
        await _wait_idle(app, pilot)
        assert ("a1", {"to": "other@x.com"}) in client.edited
        assert ("r1", "approved_with_edits") in client.resumed
