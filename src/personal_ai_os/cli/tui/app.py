"""Textual application shell for the CLI v2 TUI (T21, T30-T36).

Layout: status bar (top) · transcript · composer · key hints (bottom).
The app holds no network logic beyond the injected client; the
:class:`~personal_ai_os.cli.tui.controllers.chat.ChatController` drives runs
and approval resolution.
"""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
from urllib.parse import urlparse

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header

from personal_ai_os.cli.api.client import AsyncAPIClient
from personal_ai_os.cli.bootstrap import build_client
from personal_ai_os.cli.domain.state import (
    apply_provider_status,
    begin_provider_status_query,
    fail_provider_status_query,
)
from personal_ai_os.cli.sanitize import redact_secrets, strip_control_sequences
from personal_ai_os.cli.tui.command_registry import list_commands, lookup
from personal_ai_os.cli.tui.controllers.chat import ChatController
from personal_ai_os.cli.tui.keymap import BINDINGS as KEYMAP_BINDINGS
from personal_ai_os.cli.tui.model_flow import (
    ModelScreenData,
    ModelSelection,
    load_model_screen_data,
    save_model_selection,
)
from personal_ai_os.cli.tui.render import provider_status_lines
from personal_ai_os.cli.tui.screens.api_config import (
    APIConfigRequest,
    APIConfigScreen,
)
from personal_ai_os.cli.tui.screens.approval import ApprovalScreen
from personal_ai_os.cli.tui.screens.confirm import ConfirmScreen
from personal_ai_os.cli.tui.screens.info import InfoScreen
from personal_ai_os.cli.tui.screens.model import ModelScreen
from personal_ai_os.cli.tui.widgets.composer import Composer
from personal_ai_os.cli.tui.widgets.provider_notice import ProviderNotice
from personal_ai_os.cli.tui.widgets.status_bar import StatusBar
from personal_ai_os.cli.tui.widgets.transcript import Transcript
from personal_ai_os.model_gateway import ProviderConfigStore, ProviderProfile

_CSS = """
Screen { layout: vertical; }
#transcript {
    height: auto;
    padding: 0 1;
}
#scroll {
    height: 1fr;
    border-bottom: solid $border;
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
#provider-notice {
    height: auto;
    background: $warning-muted;
    color: $text;
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
#api-panel {
    width: 80%;
    height: auto;
    max-height: 95%;
    border: round $accent;
    background: $surface;
    padding: 1 2;
}
#api-title { text-style: bold; margin-bottom: 1; }
#api-runtime { color: $text-muted; margin-bottom: 1; }
#api-actions { height: auto; margin-top: 1; }
#api-actions Button { margin-right: 1; }
#api-error { color: $error; height: auto; }
#api-key-state { color: $text-muted; height: auto; }
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
        provider_store: ProviderConfigStore | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._session_id = session_id
        self._owns_client = client is None
        self._provider_store = provider_store or ProviderConfigStore()
        self.controller: ChatController | None = None
        self._model_screen_data: ModelScreenData | None = None
        self._approval_open = False
        self._pending_memory_forget: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield StatusBar(id="status")
        yield ProviderNotice(id="provider-notice")
        yield VerticalScroll(Transcript(id="transcript"), id="scroll")
        yield Composer(id="composer")
        yield Footer()

    async def on_mount(self) -> None:
        if self._client is None:
            self._client = build_client()
        self.controller = ChatController(
            self._client, self, session_id=self._session_id
        )
        await self.controller.restore_session()
        await self._refresh_provider_status()
        self.refresh_ui()
        self.query_one("#composer", Composer).focus()

    async def on_unmount(self) -> None:
        # Cancel any in-flight stream task so its finally block never refreshes
        # a torn-down DOM (F6.1).
        if self.controller is not None and self.controller._stream_task is not None:
            task = self.controller._stream_task
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    # -- rendering ---------------------------------------------------------

    def refresh_ui(self) -> None:
        """Repaint status + transcript; surface a pending approval modal."""
        if self.controller is None:
            return
        state = self.controller.state
        self.query_one("#status", StatusBar).render_state(state)
        self.query_one("#provider-notice", ProviderNotice).render_state(state)
        self.query_one("#transcript", Transcript).render_state(state)
        self.query_one("#composer", Composer).set_disabled(self.controller.busy)
        if self.controller.pending_approval and not self._approval_open:
            self._approval_open = True
            from personal_ai_os.cli.notify import approval_required

            approval_required()
            self.push_screen(
                ApprovalScreen(dict(self.controller.pending_approval)),
                callback=self._on_approval_dismiss,
            )

    async def _on_approval_dismiss(self, result) -> None:  # noqa: ANN001
        self._approval_open = False
        if self.controller is None:
            return
        if result is None:
            # Escape = dismiss the card in the UI (the run stays waiting on the
            # server; the user can re-open via /approvals or a new flow).
            self.controller.pending_approval = None
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
            lines.append(f"args   : {json.dumps(redact_secrets(tool.arguments_preview), ensure_ascii=False)}")
            if tool.summary:
                lines.append(f"summary: {strip_control_sequences(tool.summary)}")
            if tool.result_preview:
                lines.append(f"result : {strip_control_sequences(tool.result_preview)}")
            if tool.error:
                lines.append(f"error  : {strip_control_sequences(tool.error)}")
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
            f"API endpoint: {self._client.base_url if self._client else '-'}",
        ]
        lines.extend(provider_status_lines(state))
        if state.pending_approval:
            lines.append(f"Approval  : pending ({state.pending_approval.get('tool_name', '?')})")
        if state.last_error:
            lines.append(f"Last error: {strip_control_sequences(state.last_error)}")
        self.push_screen(InfoScreen("Status", lines))

    async def _refresh_provider_status(self) -> None:
        if self.controller is None or self._client is None:
            return
        begin_provider_status_query(self.controller.state)
        try:
            status = await self._client.get_provider_status()
        except Exception:  # noqa: BLE001 - chat remains usable if status is unavailable
            fail_provider_status_query(self.controller.state)
            return
        apply_provider_status(self.controller.state, status)

    async def cmd_api(self, _args: str) -> None:
        if self.controller is None or self._client is None:
            return
        if self.controller.busy:
            self.notify(
                "A run is active; cancel it before switching provider",
                timeout=3,
            )
            return
        if not _is_local_api(self._client.base_url):
            self.notify(
                "Provider profiles can only be changed for a local API; "
                "configure the remote server instead",
                timeout=5,
            )
            return
        try:
            status = await self._client.get_provider_status()
        except Exception as exc:  # noqa: BLE001
            self.notify(
                f"provider status error: {self._provider_error(exc)}",
                timeout=4,
            )
            return
        summary = (
            f"Runtime: {status.provider} · {status.model}"
            if status.mode != "echo"
            else "Runtime: Echo (no provider configured)"
        )
        self.push_screen(
            APIConfigScreen(
                self._provider_store.list_profiles(),
                active_name=self._provider_store.get_active_name(),
                runtime_summary=summary,
            ),
            callback=self._on_api_config,
        )

    async def _on_api_config(self, request: APIConfigRequest | None) -> None:
        if request is None or self._client is None:
            return
        if self.controller is not None and self.controller.busy:
            self.notify(
                "A run is active; provider configuration was not changed",
                timeout=3,
            )
            return

        previous_active = self._provider_store.get_active_name()
        previous_profile = copy.deepcopy(
            self._provider_store.get(request.profile_name)
        )
        changed = False
        try:
            if request.action == "activate":
                if not self._provider_store.set_active(request.profile_name):
                    raise ValueError(f"Unknown profile {request.profile_name!r}")
            elif request.action == "add":
                self._provider_store.add(
                    ProviderProfile(
                        name=request.profile_name,
                        format=request.format,
                        api_key=request.api_key,
                        base_url=request.base_url,
                        model=request.model,
                    )
                )
            elif request.action == "update":
                fields = {
                    "format": request.format,
                    "base_url": request.base_url,
                    "model": request.model,
                }
                if request.api_key:
                    fields["api_key"] = request.api_key
                if self._provider_store.update(
                    request.profile_name, **fields
                ) is None:
                    raise ValueError(f"Unknown profile {request.profile_name!r}")
                self._provider_store.set_active(request.profile_name)
            else:
                raise ValueError("Unsupported provider configuration action")
            changed = True
            status = await self._client.reload_provider(
                str(self._provider_store.path.parent)
            )
        except Exception as exc:  # noqa: BLE001
            if changed:
                self._rollback_provider_change(
                    request, previous_active, previous_profile
                )
            self.notify(
                f"provider configuration failed: "
                f"{self._provider_error(exc, request.api_key)}",
                timeout=5,
            )
            return

        if self.controller is not None:
            apply_provider_status(self.controller.state, status)
            self.refresh_ui()
        self.notify(
            f"Provider active: {status.profile or status.provider} · {status.model}",
            timeout=3,
        )

    async def cmd_model(self, _args: str) -> None:
        if self.controller is None or self._client is None:
            return
        if self.controller.busy:
            self.notify(
                "A run is active; cancel it before switching model",
                timeout=3,
            )
            return
        if not _is_local_api(self._client.base_url):
            self.notify(
                "Models can only be changed for a local API with shared "
                "configuration; configure the remote server instead",
                timeout=5,
            )
            return
        try:
            data = await load_model_screen_data(self._client)
        except Exception as exc:  # noqa: BLE001
            self.notify(
                f"model configuration error: {self._provider_error(exc)}",
                timeout=4,
            )
            return
        if data.provider == "echo":
            self.notify("Configure an LLM provider with /api before selecting a model")
            return
        self._model_screen_data = data
        self.push_screen(ModelScreen(data), callback=self._on_model_selection)

    async def _on_model_selection(
        self, selection: ModelSelection | None
    ) -> None:
        data = self._model_screen_data
        self._model_screen_data = None
        if selection is None or data is None or self._client is None:
            return
        if self.controller is not None and self.controller.busy:
            self.notify(
                "A run is active; model configuration was not changed",
                timeout=3,
            )
            return
        try:
            status = await save_model_selection(
                self._client,
                config_dir=str(self._provider_store.path.parent),
                screen_data=data,
                selection=selection,
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(
                f"model configuration failed: {self._provider_error(exc)}",
                timeout=5,
            )
            return
        if self.controller is not None:
            apply_provider_status(self.controller.state, status)
            self.refresh_ui()
        self.notify(
            f"Model active: {status.model} · effort {status.reasoning_effort}",
            timeout=3,
        )

    def _rollback_provider_change(
        self,
        request: APIConfigRequest,
        previous_active: str | None,
        previous_profile: ProviderProfile | None,
    ) -> None:
        try:
            if request.action == "add":
                self._provider_store.remove(request.profile_name)
            elif request.action == "update" and previous_profile is not None:
                self._provider_store.update(
                    previous_profile.name,
                    format=previous_profile.format,
                    base_url=previous_profile.base_url,
                    model=previous_profile.model,
                    reasoning_effort=previous_profile.reasoning_effort,
                    max_tokens=previous_profile.max_tokens,
                    api_key=previous_profile.api_key,
                )
                if not previous_profile.api_key:
                    self._provider_store.vault.delete(previous_profile.name)
            if previous_active is not None:
                self._provider_store.set_active(previous_active)
            else:
                self._provider_store.clear_active()
        except Exception:  # noqa: BLE001 - keep the original safe error surface
            pass

    def _provider_error(self, exc: Exception, api_key: str = "") -> str:
        message = strip_control_sequences(str(exc))
        secrets = [api_key]
        secrets.extend(
            profile.api_key for profile in self._provider_store.list_profiles()
        )
        for secret in secrets:
            if secret:
                message = message.replace(secret, "***")
        return message

    async def cmd_context(self, _args: str) -> None:
        if self.controller is None or self._client is None:
            return
        run = None
        if self.controller.state.run_id:
            try:
                run = await self._client.get_run(self.controller.state.run_id)
            except Exception as exc:  # noqa: BLE001
                self.notify(f"context error: {exc}", timeout=3)
                return
        self.push_screen(
            InfoScreen("Context summary", context_summary_lines(self.controller.state, run))
        )

    async def cmd_memory(self, args: str) -> None:
        if self._client is None:
            return
        parts = args.strip().split(maxsplit=1)
        try:
            if parts and parts[0].lower() == "show" and len(parts) == 2:
                memory = await self._client.get_memory(parts[1])
                self.push_screen(InfoScreen("Memory detail", memory_detail_lines(memory)))
                return
            if parts and parts[0].lower() == "forget" and len(parts) == 2:
                memory = await self._client.get_memory(parts[1])
                self._pending_memory_forget = memory.id
                self.push_screen(
                    ConfirmScreen(
                        "Forget memory?",
                        f"{memory.id[:8]} · {strip_control_sequences(memory.summary or memory.content[:100])}",
                    ),
                    callback=self._on_memory_forget_confirm,
                )
                return
            query = args.strip()
            if query:
                memories = await self._client.search_memories(query, limit=20)
            else:
                memories = await self._client.list_memories()
        except Exception as exc:  # noqa: BLE001
            self.notify(f"memory error: {exc}", timeout=3)
            return
        lines = [memory_summary_line(memory) for memory in memories]
        if not lines:
            lines = ["no memories"]
        self.push_screen(InfoScreen(f"Memory ({len(memories)})", lines))

    async def _on_memory_forget_confirm(self, confirmed: bool) -> None:
        memory_id = self._pending_memory_forget
        self._pending_memory_forget = None
        if not confirmed or not memory_id or self._client is None:
            return
        try:
            await self._client.forget_memory(memory_id)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"memory error: {exc}", timeout=3)
            return
        self.notify(f"Forgot memory {memory_id[:8]}", timeout=2)

    async def cmd_tools(self, _args: str) -> None:
        if self._client is None:
            return
        try:
            tools = await self._client.list_tools()
        except Exception as exc:  # noqa: BLE001
            self.notify(f"tools error: {exc}", timeout=3)
            return
        lines = [f"R{t.risk_level}  {strip_control_sequences(t.name)}  [dim]{strip_control_sequences(t.description[:60])}[/]" for t in tools]
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
            f"[dim]{a.id[:8]}[/] R{a.risk_level}  {strip_control_sequences(a.tool_name)}  {strip_control_sequences(a.action_summary)}" for a in approvals
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
    client: AsyncAPIClient | None = None,
    *,
    session_id: str | None = None,
    provider_store: ProviderConfigStore | None = None,
) -> PersonalAIApp:
    """App factory (used by tests and the plain fallback entry)."""
    return PersonalAIApp(
        client=client,
        session_id=session_id,
        provider_store=provider_store,
    )


def _is_local_api(base_url: str) -> bool:
    hostname = (urlparse(base_url).hostname or "").lower()
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def context_summary_lines(state, run) -> list[str]:  # noqa: ANN001
    """Build a metadata-only context summary; never expose prompt contents."""
    run_state = dict(getattr(run, "state", None) or {})
    context_items = run_state.get("context_items") or []
    messages = run_state.get("messages") or []
    tool_results = run_state.get("tool_results") or []
    summary_present = bool(run_state.get("conversation_summary"))
    memory_count = sum(
        1
        for item in context_items
        if isinstance(item, dict)
        and str(item.get("type") or item.get("source") or "").lower().startswith("memory")
    )
    usage = dict(getattr(run, "model_usage", None) or {})
    total_tokens = usage.get("total_tokens")
    return [
        f"Session             : {state.session_id or 'unknown'}",
        f"Run                 : {state.run_id or 'unknown'}",
        f"Conversation turns  : {len(messages) if messages else 'unknown'}",
        f"Conversation summary: {'present' if summary_present else 'not reported'}",
        f"Context sources      : {len(context_items) if context_items else 'not reported'}",
        f"Memories used        : {memory_count if context_items else 'not reported'}",
        f"Tool results         : {len(tool_results)}",
        f"Model tokens         : {total_tokens if total_tokens is not None else 'unknown'}",
    ]


def memory_summary_line(memory) -> str:  # noqa: ANN001
    source = f"{memory.source_type}:{memory.source_id or 'unknown'}"
    content = strip_control_sequences(memory.summary or memory.content[:120])
    return f"[dim]{memory.id[:8]} · {memory.type} · {memory.scope} · {source}[/] {content}"


def memory_detail_lines(memory) -> list[str]:  # noqa: ANN001
    return [
        f"ID        : {memory.id}",
        f"Type      : {memory.type}",
        f"Scope     : {memory.scope}",
        f"Source    : {memory.source_type}:{memory.source_id or 'unknown'}",
        f"Sensitivity: {memory.sensitivity}",
        f"Confidence: {memory.confidence}",
        f"Summary   : {strip_control_sequences(memory.summary or 'not reported')}",
        f"Content   : {strip_control_sequences(memory.content)}",
    ]
