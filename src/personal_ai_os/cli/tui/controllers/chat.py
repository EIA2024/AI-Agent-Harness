"""TUI chat controller (CLI v2, T21/T24/T30/T31/T32).

Side-effectful actions (send, cancel, approve/reject/edit + resume) run here;
the controller drives the SSE stream through the shared normalize → reduce
pipeline and asks the app to repaint. Widgets never touch the network.

Approval flow (T31/T32): when ``approval.required`` arrives the server closes
the stream. The controller fetches the pending approval record, then
``resolve_approval`` posts the decision + resume and re-subscribes to the
stream, preserving the transcript.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from personal_ai_os.cli.api.client import AsyncAPIClient
from personal_ai_os.cli.api.errors import APIError, TransportError
from personal_ai_os.cli.domain.events import UIEventType
from personal_ai_os.cli.domain.normalizer import Normalizer
from personal_ai_os.cli.domain.reducer import reduce
from personal_ai_os.cli.domain.state import CELL_USER, TranscriptCell, initial_state

_REFRESH_INTERVAL = 0.05  # coalesce rapid deltas (plan §8)

# approval decision → server resume `decision` value
_DECISIONS = {
    "approve": "approved",
    "reject": "rejected",
    "edit": "approved_with_edits",
}


class ChatController:
    """Drives one session's runs through the shared presentation pipeline."""

    def __init__(
        self, client: AsyncAPIClient, app: Any, *, session_id: str | None = None
    ) -> None:
        self.client = client
        self.app = app
        self.state = initial_state(session_id=session_id)
        self._normalizer = Normalizer()
        self._stream_task: asyncio.Task | None = None
        self._last_refresh = 0.0
        self.pending_approval: dict[str, Any] | None = None

    @property
    def busy(self) -> bool:
        return self._stream_task is not None and not self._stream_task.done()

    async def ensure_session(self) -> None:
        if not self.state.session_id:
            created = await self.client.create_session(channel="cli")
            self.state.session_id = created.id
            self._refresh()

    async def send(self, prompt: str) -> bool:
        """Submit a prompt; spawn the stream task. Returns False if busy."""
        if self.busy:
            return False
        self._append_user(prompt)
        await self.ensure_session()
        send = await self.client.send_message(self.state.session_id, prompt)
        if send.run_id is None:
            self.state.last_error = "server did not return a run id"
            self._refresh()
            return True
        self.state.run_id = send.run_id
        self._refresh()
        self._stream_task = asyncio.create_task(self._stream(send.run_id))
        return True

    async def _stream(self, run_id: str) -> None:
        try:
            async for server_event in self.client.stream_run(run_id):
                event = self._normalizer.normalize(server_event, run_id=run_id)
                reduce(self.state, event)
                if event.type == UIEventType.APPROVAL_REQUIRED:
                    await self._load_pending_approval()
                self._refresh()
        except (APIError, TransportError) as exc:
            self.state.last_error = str(exc)
        finally:
            self._refresh(force=True)

    async def _load_pending_approval(self) -> None:
        """Populate the pending-approval card.

        Prefers the T43-enriched event payload (approval_id/tool_name/…); older
        servers only signal ``status``, so fall back to a pending-list fetch.
        """
        event_payload = dict(self.state.pending_approval or {})
        if event_payload.get("approval_id"):
            self.pending_approval = {
                "id": event_payload["approval_id"],
                "run_id": self.state.run_id,
                "tool_name": event_payload.get("tool_name", ""),
                "action_summary": event_payload.get("action_summary", ""),
                "risk_level": event_payload.get("risk_level", 0),
                "status": "pending",
            }
            return
        try:
            approvals = await self.client.list_approvals(status="pending")
        except (APIError, TransportError):
            self.pending_approval = {"status": "waiting_approval"}
            return
        match = next(
            (a for a in approvals if a.run_id == self.state.run_id), None
        ) or (approvals[0] if approvals else None)
        if match is not None:
            self.pending_approval = {
                "id": match.id,
                "run_id": match.run_id,
                "tool_name": match.tool_name,
                "action_summary": match.action_summary,
                "risk_level": match.risk_level,
                "arguments_preview": match.arguments_preview or {},
                "status": match.status,
            }
        else:
            self.pending_approval = {"status": "waiting_approval"}

    async def resolve_approval(
        self, action: str, *, edited_arguments: dict[str, Any] | None = None
    ) -> None:
        """Approve/reject/edit a pending approval and resume the run."""
        if self.pending_approval is None or self.pending_approval.get("id") is None:
            return
        approval_id = self.pending_approval["id"]
        decision = _DECISIONS.get(action)
        if decision is None:
            return
        try:
            if action == "approve":
                await self.client.approve(approval_id)
            elif action == "reject":
                await self.client.reject(approval_id)
            elif action == "edit" and edited_arguments is not None:
                await self.client.edit_approval(approval_id, edited_arguments)
            self.pending_approval = None
            run_id = self.state.run_id
            if run_id:
                await self.client.resume_run(run_id, approval_id=approval_id, decision=decision)
                self._stream_task = asyncio.create_task(self._stream(run_id))
        except (APIError, TransportError) as exc:
            self.state.last_error = str(exc)
            self._refresh(force=True)

    async def cancel(self) -> None:
        """Cancel the active run (server-side), keep the transcript."""
        run_id = self.state.run_id
        if not run_id:
            return
        try:
            await self.client.cancel_run(run_id)
        except (APIError, TransportError) as exc:
            self.state.last_error = str(exc)
        finally:
            self._refresh(force=True)

    def _append_user(self, prompt: str) -> None:
        self.state.transcript.append(
            TranscriptCell(kind=CELL_USER, payload={"text": prompt}, text=prompt)
        )

    def _refresh(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_refresh >= _REFRESH_INTERVAL:
            self._last_refresh = now
            self.app.refresh_ui()


def build_chat_controller(
    app: Any, client: AsyncAPIClient, *, session_id: str | None = None
) -> ChatController:
    return ChatController(client=client, app=app, session_id=session_id)
