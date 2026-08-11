"""Textual application shell for the CLI v2 TUI (T21).

Layout: status bar (top) · transcript · composer · key hints (bottom).
The app holds no network logic beyond the injected client; the
:class:`~personal_ai_os.cli.tui.controllers.chat.ChatController` drives runs.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header

from personal_ai_os.cli.api.client import AsyncAPIClient
from personal_ai_os.cli.bootstrap import build_client
from personal_ai_os.cli.tui.controllers.chat import ChatController
from personal_ai_os.cli.tui.widgets.composer import Composer
from personal_ai_os.cli.tui.widgets.status_bar import StatusBar
from personal_ai_os.cli.tui.widgets.transcript import Transcript

_CSS = """
#transcript {
    height: 1fr;
    border-bottom: solid $border;
    padding: 0 1;
}
#composer {
    height: auto;
    max-height: 6;
    border-top: solid $border;
    margin: 0 1;
}
#status {
    height: 1;
    background: $surface;
    color: $text-muted;
    padding: 0 1;
}
"""


class PersonalAIApp(App):
    TITLE = "Personal AI"
    CSS = _CSS
    BINDINGS = [
        ("ctrl+c", "interrupt", "Interrupt"),
        ("ctrl+l", "clear_view", "Clear view"),
    ]

    def __init__(self, client: AsyncAPIClient | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._owns_client = client is None
        self.controller: ChatController | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield StatusBar(id="status")
        yield VerticalScroll(Transcript(id="transcript"), id="scroll")
        yield Composer(id="composer")
        yield Footer()

    async def on_mount(self) -> None:
        if self._client is None:
            self._client = build_client()
        self.controller = ChatController(self._client, self)
        self.refresh_ui()
        self.query_one("#composer", Composer).focus()

    async def on_unmount(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def refresh_ui(self) -> None:
        """Repaint status + transcript from the presentation state."""
        if self.controller is None:
            return
        state = self.controller.state
        self.query_one("#status", StatusBar).render_state(state)
        self.query_one("#transcript", Transcript).render_state(state)
        composer = self.query_one("#composer", Composer)
        composer.set_disabled(self.controller.busy)

    async def on_composer_submitted(self, message: Composer.Submitted) -> None:
        if self.controller is None or self.controller.busy:
            return
        try:
            await self.controller.send(message.text)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the TUI
            self.controller.state.last_error = str(exc)
            self.refresh_ui()

    async def action_interrupt(self) -> None:
        """Ctrl+C: active run → cancel; otherwise exit."""
        if self.controller is not None and self.controller.busy:
            self.refresh_ui()
            await self.controller.cancel()
            self.notify("Cancelling run…", timeout=2)
        else:
            self.exit()

    def action_clear_view(self) -> None:
        """Ctrl+L: clear the visible transcript (session state untouched)."""
        if self.controller is not None:
            self.controller.state.transcript.clear()
            self.refresh_ui()


def create_app(client: AsyncAPIClient | None = None) -> PersonalAIApp:
    """App factory (used by tests and the plain fallback entry)."""
    return PersonalAIApp(client=client)
