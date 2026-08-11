"""Shared fixtures for CLI unit tests.

The legacy CLI module builds its own ``httpx.Client`` internally, so the tests
mock HTTP by patching the module's ``httpx.Client`` with a transport that
answers from an in-memory route table. No real network is ever touched.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest


class FakeAPI:
    """In-memory HTTP responder keyed by (method, path).

    ``routes`` maps ``(method, path)`` to either a dict (200 JSON response) or
    an int status code (error response with ``{"detail": "boom"}``).
    """

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = (request.method, request.url.path)
        if key in self.routes:
            value = self.routes[key]
            if isinstance(value, int):
                return httpx.Response(value, json={"detail": "boom"})
            return httpx.Response(200, json=value)
        return httpx.Response(404, json={"detail": f"no route for {key}"})


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[tuple[str, str], Any]], FakeAPI]:
    """Patch the legacy module's httpx.Client with a MockTransport responder."""

    def make(routes: dict[tuple[str, str], Any]) -> FakeAPI:
        api = FakeAPI(routes)
        real_client = httpx.Client

        def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
            kwargs.setdefault("transport", httpx.MockTransport(api.handler))
            return real_client(*args, **kwargs)

        import personal_ai_os.cli.legacy as legacy

        monkeypatch.setattr(legacy.httpx, "Client", client_factory)
        return api

    return make
