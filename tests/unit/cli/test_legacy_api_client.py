"""Contract tests for the legacy sync APIClient (CLI v2 Phase 0, T02).

The compatibility client now shares the v2 rule that transports never print
or raise ``SystemExit``; only the command boundary renders typed failures.
"""

from __future__ import annotations

import httpx
import pytest

from personal_ai_os.cli import legacy
from personal_ai_os.cli.api.errors import ServerError


def _echo_handler(request: httpx.Request) -> httpx.Response:
    """Return request echo for transport-level assertions."""
    if request.url.path == "/ok":
        return httpx.Response(200, json={"hello": "world"})
    if request.url.path == "/boom":
        return httpx.Response(500, json={"detail": "internal"})
    if request.url.path == "/text-boom":
        return httpx.Response(502, text="bad gateway raw")
    return httpx.Response(404, json={"detail": "nope"})


def _client_with(handler) -> legacy.APIClient:  # noqa: ANN001
    c = legacy.APIClient(base_url="http://test", api_key="k")
    c._http = httpx.Client(transport=httpx.MockTransport(handler))
    return c


def test_request_header_includes_api_key():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-API-Key", ""))
        return httpx.Response(200, json={})

    client = _client_with(handler)
    client.get("/ok")
    assert seen == ["k"]


def test_request_builds_url_from_base():
    client = _client_with(_echo_handler)
    assert client.base_url == "http://test"
    assert client.request("GET", "/ok") == {"hello": "world"}


def test_get_post_delete_delegate_to_request():
    client = _client_with(_echo_handler)
    assert client.get("/ok") == {"hello": "world"}
    assert client.post("/ok") == {"hello": "world"}
    assert client.delete("/ok") == {"hello": "world"}


def test_error_is_typed_and_transport_prints_nothing(capsys):
    client = _client_with(_echo_handler)
    with pytest.raises(ServerError) as exc:
        client.get("/boom")
    assert exc.value.status_code == 500
    assert exc.value.detail == "internal"
    assert capsys.readouterr().err == ""


def test_non_json_error_body_does_not_crash(capsys):
    client = _client_with(_echo_handler)
    with pytest.raises(ServerError) as exc:
        client.get("/text-boom")
    assert exc.value.detail == "bad gateway raw"
    assert capsys.readouterr().err == ""
