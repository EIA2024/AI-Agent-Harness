"""TUI chat controller (CLI v2, T21/T24).

Side-effectful actions (send, cancel) run here; the controller drives the SSE
stream through the shared normalize → reduce pipeline and asks the app to
repaint. Widgets never touch the network.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from personal_ai_os.cli.api.client import AsyncAPIClient
from personal_ai_os.cli.api.errors import APIError, TransportError
from personal_ai_os.cli.domain.normalizer import Normalizer
from personal_ai_os.cli.domain.reducer import reduce
from personal_ai_os.cli.domain.state import CELL_USER, TranscriptCell, initial_state

_REFRESH_INTERVAL = 0.05  # coalesce rapid deltas (plan §8)


class ChatController:
    """Drives one session's runs through the shared presentation pipeline."""

    def __init__(self, client: AsyncAPIClient, app: Any) -> None:
        self.client = client
        self.app = app
        self.state = initial_state()
        self._normalizer = Normalizer()
        self._stream_task: asyncio.Task | None = None
        self._last_refresh = 0.0

    @property
    def busy(self) -> bool:
        return self._stream_task is not None and not self._stream_task.done()

    async def send(self, prompt: str) -> bool:
        """Submit a prompt; spawn the stream task. Returns False if busy."""
        if self.busy:
            return False
        self._append_user(prompt)
        if not self.state.session_id:
            created = await self.client.create_session(channel="cli")
            self.state.session_id = created.id
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
                self._refresh()
        except (APIError, TransportError) as exc:
            self.state.last_error = str(exc)
        finally:
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


def build_chat_controller(app: Any, client: AsyncAPIClient) -> ChatController:
    return ChatController(client=client, app=app)
