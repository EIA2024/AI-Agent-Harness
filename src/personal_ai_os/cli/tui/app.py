"""Textual application shell for the CLI v2 TUI (T21, T30-T36).

Layout: status bar (top) · transcript · composer · key hints (bottom).
The app holds no network logic beyond the injected client; the
:class:`~personal_ai_os.cli.tui.controllers.chat.ChatController` drives runs
and approval resolution.
"""

from __future__ import annotations

import json

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header

from personal_ai_os.cli.api.client import AsyncAPIClient
from personal_ai_os.cli.bootstrap import build_client
from personal_ai_os.cli.tui.command_registry import list_commands, lookup
from personal_ai_os.cli.tui.controllers.chat import ChatController
from personal_ai_os.cli.tui.keymap import BINDINGS as KEYMAP_BINDINGS
from personal_ai_os.cli.tui.screens.approval import ApprovalScreen
from personal_ai_os.cli.tui.screens.info import InfoScreen
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
.approval-panel {
    width: 80%;
    height: auto;
    border: round $accent;
    background: $surface;
    padding: 1 2;
}
#approval-title { text-style: bold; }
#info-panel {
    width: 80%;
    height: 60%;
    border: round $accent;
    background: $surface;
    padding: 1 2;
}
#info-title { text-style: bold; margin-bottom: 1; }
"""


class PersonalAIApp(App):
    TITLE = "Personal AI"
    CSS = _CSS
    BINDINGS = [
        ("ctrl+c", "interrupt", "Interrupt"),
        ("ctrl+l", "clear_view", "Clear view"),
        ("ctrl+o", "tool_detail", "Tool details"),
        ("ctrl+p", "status", "Status"),
    ]

    def __init__(
        self,
        client: AsyncAPIClient | None = None,
        *,
        session_id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._session_id = session_id
        self._owns_client = client is None
        self.controller: ChatController | None = None
        self._approval_open = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield StatusBar(id="status")
        yield VerticalScroll(Transcript(id="transcript"), id="scroll")
        yield Composer(id="composer")
        yield Footer()

    async def on_mount(self) -> None:
        if self._client is None:
            self._client = build_client()
        self.controller = ChatController(
            self._client, self, session_id=self._session_id
        )
        self.refresh_ui()
        self.query_one("#composer", Composer).focus()

    async def on_unmount(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    # -- rendering ---------------------------------------------------------

    def refresh_ui(self) -> None:
        """Repaint status + transcript; surface a pending approval modal."""
        if self.controller is None:
            return
        state = self.controller.state
        self.query_one("#status", StatusBar).render_state(state)
        self.query_one("#transcript", Transcript).render_state(state)
        self.query_one("#composer", Composer).set_disabled(self.controller.busy)
        if self.controller.pending_approval and not self._approval_open:
            self._approval_open = True
            self.push_screen(
                ApprovalScreen(dict(self.controller.pending_approval)),
                callback=self._on_approval_dismiss,
            )

    async def _on_approval_dismiss(self, result) -> None:  # noqa: ANN001
        self._approval_open = False
        if result is None or self.controller is None:
            return
        action, edited = result
        if action == "cancel":
            await self.controller.cancel()
            return
        await self.controller.resolve_approval(action, edited_arguments=edited)
        self.refresh_ui()

    # -- input -------------------------------------------------------------

    async def on_composer_submitted(self, message: Composer.Submitted) -> None:
        if self.controller is None:
            return
        text = message.text.strip()
        if text.startswith("/"):
            await self._run_slash_command(text)
            return
        if self.controller.busy:
            self.notify("A run is active — wait or press Ctrl+C to cancel", timeout=3)
            return
        try:
            await self.controller.send(text)
        except Exception as exc:  # noqa: BLE001 - surface, never crash the TUI
            self.controller.state.last_error = str(exc)
            self.refresh_ui()

    async def _run_slash_command(self, raw: str) -> None:
        parts = raw[1:].split(maxsplit=1)
        name = (parts[0] if parts else "").lower()
        args = parts[1] if len(parts) > 1 else ""
        spec = lookup(name)
        if spec is None:
            self.notify(f"Unknown command /{name} — type /help", timeout=3)
            return
        handler = getattr(self, spec.action, None)
        if handler is None:
            self.notify(f"/{name}: not implemented", timeout=3)
            return
        result = handler(args)
        if hasattr(result, "__await__"):
            await result

    # -- actions (keymap + slash commands) ---------------------------------

    async def action_interrupt(self) -> None:
        """Ctrl+C: active run → cancel; otherwise exit."""
        if self.controller is not None and self.controller.busy:
            await self.controller.cancel()
            self.notify("Cancelling run…", timeout=2)
        else:
            self.exit()

    def action_clear_view(self) -> None:
        if self.controller is not None:
            self.controller.state.transcript.clear()
            self.refresh_ui()

    def action_tool_detail(self) -> None:
        """Ctrl+O: expand the most recent tool cell."""
        if self.controller is None:
            return
        state = self.controller.state
        last_tool = None
        for cell in reversed(state.transcript):
            if cell.kind == "tool":
                last_tool = cell
                break
        if last_tool is None:
            self.notify("No tool cell to expand", timeout=2)
            return
        call_id = last_tool.payload.get("tool_call_id")
        tool = state.tool_calls.get(call_id) if call_id else None
        lines = [
            f"tool   : {last_tool.payload.get('tool_name', '')}",
            f"status : {tool.status if tool else last_tool.payload.get('status', '')}",
        ]
        if tool is not None:
            lines.append(f"risk   : R{tool.risk_level}" if tool.risk_level else "risk   : R0")
            lines.append(f"args   : {json.dumps(tool.arguments_preview, ensure_ascii=False)}")
            if tool.summary:
                lines.append(f"summary: {tool.summary}")
            if tool.result_preview:
                lines.append(f"result : {tool.result_preview}")
            if tool.error:
                lines.append(f"error  : {tool.error}")
        self.push_screen(InfoScreen("Tool detail", lines))

    async def action_status(self) -> None:
        await self.cmd_status("")

    async def cmd_help(self, _args: str) -> None:
        lines = [f"[bold]{kb.key}[/]  {kb.description}" for kb in KEYMAP_BINDINGS]
        lines.append("")
        lines.append("Slash commands:")
        lines += [f"/{c.name}  {c.description}" for c in list_commands()]
        self.push_screen(InfoScreen("Help", lines))

    async def cmd_status(self, _args: str) -> None:
        if self.controller is None:
            return
        state = self.controller.state
        lines = [
            f"Session   : {state.session_id or '-'}",
            f"Run       : {state.run_status} · {state.run_id or '-'}",
            f"Model     : {state.model or 'unknown'}",
            f"API       : {self._client.base_url if self._client else '-'}",
            f"Connection: {state.connection_state.value}",
        ]
        if state.pending_approval:
            lines.append(f"Approval  : pending ({state.pending_approval.get('tool_name', '?')})")
        if state.last_error:
            lines.append(f"Last error: {state.last_error}")
        self.push_screen(InfoScreen("Status", lines))

    async def cmd_memory(self, args: str) -> None:
        if self._client is None:
            return
        query = args.strip()
        try:
            if query:
                memories = await self._client.search_memories(query, limit=20)
            else:
                memories = await self._client.list_memories()
        except Exception as exc:  # noqa: BLE001
            self.notify(f"memory error: {exc}", timeout=3)
            return
        lines = [f"[dim]{m.id[:8]} · {m.type} · {m.scope}[/] {m.content[:80]}" for m in memories]
        if not lines:
            lines = ["no memories"]
        self.push_screen(InfoScreen(f"Memory ({len(memories)})", lines))

    async def cmd_tools(self, _args: str) -> None:
        if self._client is None:
            return
        try:
            tools = await self._client.list_tools()
        except Exception as exc:  # noqa: BLE001
            self.notify(f"tools error: {exc}", timeout=3)
            return
        lines = [f"R{t.risk_level}  {t.name}  [dim]{t.description[:50]}[/]" for t in tools]
        self.push_screen(InfoScreen(f"Tools ({len(tools)})", lines))

    async def cmd_approvals(self, _args: str) -> None:
        if self._client is None:
            return
        try:
            approvals = await self._client.list_approvals(status="pending")
        except Exception as exc:  # noqa: BLE001
            self.notify(f"approvals error: {exc}", timeout=3)
            return
        lines = [
            f"[dim]{a.id[:8]}[/] R{a.risk_level}  {a.tool_name}  {a.action_summary}" for a in approvals
        ]
        self.push_screen(InfoScreen(f"Approvals ({len(approvals)})", lines))

    async def cmd_new(self, _args: str) -> None:
        if self.controller is None:
            return
        self.controller.state.session_id = None
        self.controller.state.run_id = None
        self.controller.state.run_status = "unknown"
        self.controller.state.transcript.clear()
        self.controller.state.tool_calls.clear()
        self.controller.pending_approval = None
        self.refresh_ui()
        self.notify("New session", timeout=2)

    def cmd_exit(self, _args: str) -> None:
        self.exit()


def create_app(
    client: AsyncAPIClient | None = None, *, session_id: str | None = None
) -> PersonalAIApp:
    """App factory (used by tests and the plain fallback entry)."""
    return PersonalAIApp(client=client, session_id=session_id)
