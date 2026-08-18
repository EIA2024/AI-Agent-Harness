"""Async HTTP client for the Personal AI OS REST API (CLI v2, T10).

Contract: the client never prints and never raises ``SystemExit``. It returns
DTOs, yields decoded SSE events, or raises typed exceptions from
:mod:`personal_ai_os.cli.api.errors`. All rendering is the caller's job.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from personal_ai_os.cli.api import errors
from personal_ai_os.cli.api.dto import (
    ApprovalDTO,
    AuditDTO,
    MemoryDTO,
    ProviderModelsDTO,
    ProviderStatusDTO,
    RunDTO,
    SendResult,
    SessionDTO,
    ToolDTO,
)
from personal_ai_os.cli.api.sse import ServerEvent, SSEDecoder

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_API_KEY = "dev-key"
DEFAULT_TIMEOUT = 300.0


class AsyncAPIClient:
    """Async, typed, print-free client for the Personal AI OS REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_API_URL).rstrip("/")
        self.api_key = api_key or DEFAULT_API_KEY
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncAPIClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- core request ------------------------------------------------------

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        headers.setdefault("X-API-Key", self.api_key)
        try:
            response = await self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise errors.TransportError(f"{exc.__class__.__name__}: {exc}") from exc
        if response.status_code >= 400:
            detail = _extract_detail(response)
            raise errors.classify(response.status_code, detail)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            raise errors.StreamProtocolError(
                f"server returned a non-JSON response ({response.status_code})"
            ) from None

    # -- sessions ----------------------------------------------------------

    async def create_session(self, channel: str = "cli", **extra: Any) -> SessionDTO:
        data = await self.request("POST", "/v1/sessions", json={"channel": channel, **extra})
        return SessionDTO.from_dict(data)

    async def list_sessions(
        self, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[SessionDTO]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        data = await self.request("GET", "/v1/sessions", params=params)
        return [SessionDTO.from_dict(s) for s in (data or [])]

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/v1/sessions/{session_id}")

    # -- messages / runs ---------------------------------------------------

    async def send_message(self, session_id: str, text: str) -> SendResult:
        data = await self.request(
            "POST", f"/v1/sessions/{session_id}/messages", json={"text": text}
        )
        return SendResult.from_dict(data)

    async def get_run(self, run_id: str) -> RunDTO:
        data = await self.request("GET", f"/v1/runs/{run_id}")
        return RunDTO.from_dict(data)

    async def list_runs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunDTO]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if session_id:
            params["session_id"] = session_id
        if status:
            params["status"] = status
        data = await self.request("GET", "/v1/runs", params=params)
        return [RunDTO.from_dict(r) for r in (data or [])]

    async def cancel_run(self, run_id: str) -> RunDTO:
        data = await self.request("POST", f"/v1/runs/{run_id}/cancel")
        return RunDTO.from_dict(data)

    async def resume_run(
        self,
        run_id: str,
        *,
        approval_id: str | None = None,
        decision: str | None = None,
        edited_arguments: dict[str, Any] | None = None,
    ) -> RunDTO:
        body: dict[str, Any] = {}
        if approval_id is not None:
            body["approval_id"] = approval_id
        if decision is not None:
            body["decision"] = decision
        if edited_arguments is not None:
            body["edited_arguments"] = edited_arguments
        data = await self.request("POST", f"/v1/runs/{run_id}/resume", json=body)
        return RunDTO.from_dict(data)

    # -- provider ----------------------------------------------------------

    async def get_provider_status(self) -> ProviderStatusDTO:
        data = await self.request("GET", "/v1/provider")
        return ProviderStatusDTO.from_dict(data)

    async def reload_provider(self, config_dir: str) -> ProviderStatusDTO:
        data = await self.request(
            "POST",
            "/v1/provider/reload",
            json={"config_dir": config_dir},
        )
        return ProviderStatusDTO.from_dict(data)

    async def list_provider_models(self) -> ProviderModelsDTO:
        data = await self.request("GET", "/v1/provider/models")
        return ProviderModelsDTO.from_dict(data)

    async def update_provider_model(
        self,
        *,
        config_dir: str,
        model: str,
        reasoning_effort: str = "auto",
    ) -> ProviderStatusDTO:
        data = await self.request(
            "PUT",
            "/v1/provider/model",
            json={
                "config_dir": config_dir,
                "model": model,
                "reasoning_effort": reasoning_effort,
            },
        )
        return ProviderStatusDTO.from_dict(data)

    # -- streaming ---------------------------------------------------------

    async def stream_run(self, run_id: str) -> AsyncIterator[ServerEvent]:
        """Subscribe to ``GET /v1/runs/{id}/stream`` and yield decoded events.

        A non-terminal disconnect is retried with ``Last-Event-ID``. Stable
        event IDs and sequence numbers make overlap harmless and gaps explicit.
        """
        terminal_events = {
            "run.completed",
            "run.failed",
            "run.cancelled",
            "approval.required",
        }
        last_seq: int | None = None
        last_event_id: str | None = None
        seen_event_ids: set[str] = set()
        last_transport_error: httpx.HTTPError | None = None

        for attempt in range(3):
            decoder = SSEDecoder()
            headers = {"X-API-Key": self.api_key}
            if last_event_id is not None:
                headers["Last-Event-ID"] = last_event_id
            terminal = False
            try:
                async with self._http.stream(
                    "GET", f"/v1/runs/{run_id}/stream", headers=headers
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        detail = _extract_detail_from_bytes(body)
                        raise errors.classify(response.status_code, detail)
                    async for line in response.aiter_lines():
                        for event in decoder.feed_line(line):
                            emitted, last_seq, last_event_id = _accept_stream_event(
                                event,
                                last_seq=last_seq,
                                last_event_id=last_event_id,
                                seen_event_ids=seen_event_ids,
                            )
                            for item in emitted:
                                yield item
                                terminal = terminal or item.event in terminal_events
                    for event in decoder.finish():
                        emitted, last_seq, last_event_id = _accept_stream_event(
                            event,
                            last_seq=last_seq,
                            last_event_id=last_event_id,
                            seen_event_ids=seen_event_ids,
                        )
                        for item in emitted:
                            yield item
                            terminal = terminal or item.event in terminal_events
            except httpx.HTTPError as exc:
                last_transport_error = exc
            if terminal:
                return
            if attempt < 2:
                continue

        if last_transport_error is not None and last_seq is None:
            exc = last_transport_error
            raise errors.TransportError(f"{exc.__class__.__name__}: {exc}") from exc
        raise errors.StreamProtocolError(
            "run stream ended before a terminal event after 3 reconnect attempts"
        )

    # -- approvals ---------------------------------------------------------

    async def list_approvals(self, status: str = "pending") -> list[ApprovalDTO]:
        data = await self.request("GET", "/v1/approvals", params={"status": status})
        return [ApprovalDTO.from_dict(a) for a in (data or [])]

    async def approve(self, approval_id: str) -> ApprovalDTO:
        data = await self.request("POST", f"/v1/approvals/{approval_id}/approve")
        return ApprovalDTO.from_dict(data)

    async def reject(self, approval_id: str) -> ApprovalDTO:
        data = await self.request("POST", f"/v1/approvals/{approval_id}/reject")
        return ApprovalDTO.from_dict(data)

    async def edit_approval(
        self, approval_id: str, edited_arguments: dict[str, Any]
    ) -> ApprovalDTO:
        data = await self.request(
            "POST", f"/v1/approvals/{approval_id}/edit",
            json={"edited_arguments": edited_arguments},
        )
        return ApprovalDTO.from_dict(data)

    # -- memories ----------------------------------------------------------

    async def list_memories(self, **params: Any) -> list[MemoryDTO]:
        data = await self.request("GET", "/v1/memories", params=params)
        return [MemoryDTO.from_dict(m) for m in (data or [])]

    async def search_memories(self, query: str, limit: int = 10) -> list[MemoryDTO]:
        data = await self.request(
            "POST", "/v1/memories/search", json={"query": query, "limit": limit}
        )
        return [MemoryDTO.from_dict(m) for m in (data or [])]

    async def forget_memory(self, memory_id: str) -> dict[str, Any]:
        return await self.request("DELETE", f"/v1/memories/{memory_id}")

    async def get_memory(self, memory_id: str) -> MemoryDTO:
        data = await self.request("GET", f"/v1/memories/{memory_id}")
        return MemoryDTO.from_dict(data)

    # -- tools / audit / health -------------------------------------------

    async def list_tools(self) -> list[ToolDTO]:
        data = await self.request("GET", "/v1/tools")
        return [ToolDTO.from_dict(t) for t in (data or [])]

    async def list_audit(self, limit: int = 50) -> list[AuditDTO]:
        data = await self.request("GET", "/v1/audit", params={"limit": limit})
        return [AuditDTO.from_dict(a) for a in (data or [])]

    async def healthz(self) -> dict[str, Any]:
        return await self.request("GET", "/healthz")


def _accept_stream_event(
    event: ServerEvent,
    *,
    last_seq: int | None,
    last_event_id: str | None,
    seen_event_ids: set[str],
) -> tuple[list[ServerEvent], int | None, str | None]:
    """Deduplicate one SSE event and surface any canonical sequence gap."""
    if event.malformed:
        return [event], last_seq, last_event_id

    event_id = event.id or event.data.get("event_id")
    event_id = str(event_id) if event_id else None
    if event_id is not None and event_id in seen_event_ids:
        return [], last_seq, last_event_id

    raw_seq = event.data.get("seq")
    seq = raw_seq if isinstance(raw_seq, int) else None
    if seq is not None and last_seq is not None and seq <= last_seq:
        if event_id is not None:
            seen_event_ids.add(event_id)
        return [], last_seq, last_event_id

    emitted: list[ServerEvent] = []
    if seq is not None and last_seq is not None and seq > last_seq + 1:
        emitted.append(
            ServerEvent(
                event="stream.gap",
                data={
                    "message": f"SSE gap detected: seq {last_seq} → {seq}",
                    "from_seq": last_seq,
                    "to_seq": seq,
                },
            )
        )
    emitted.append(event)
    if event_id is not None:
        seen_event_ids.add(event_id)
        last_event_id = event_id
    if seq is not None:
        last_seq = seq
    return emitted, last_seq, last_event_id


def _extract_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        detail = body.get("detail", response.text) if isinstance(body, dict) else body
        return str(detail)
    except (json.JSONDecodeError, AttributeError):
        return response.text or f"HTTP {response.status_code}"


def _extract_detail_from_bytes(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "detail" in parsed:
            return str(parsed["detail"])
    except json.JSONDecodeError:
        pass
    return text or "stream request failed"
