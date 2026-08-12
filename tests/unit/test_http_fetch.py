"""Unit tests for the HTTP fetch connector (SSRF guard, allowlist, timeout)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest

from connectors.http_fetch.connector import HttpFetchConnector
from personal_ai_os.common.models import ToolExecutionContext


def ctx() -> ToolExecutionContext:
    return ToolExecutionContext(run_id=uuid4(), owner_id=uuid4())


def mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="hello from the web")


@pytest.mark.asyncio
async def test_list_tools():
    connector = HttpFetchConnector()
    tools = await connector.list_tools()
    assert len(tools) == 2  # read fetch + mutating request (P0-002)
    tool = tools[0]
    assert tool.name == "http_fetch.fetch"
    assert tool.risk_level == 1
    assert "url" in tool.input_schema["properties"]
    assert "url" in tool.input_schema["required"]


class TestNormalFetch:
    @pytest.mark.asyncio
    async def test_get_returns_status_and_body(self):
        connector = HttpFetchConnector(transport=mock_transport(ok_handler))
        result = await connector.execute(
            "http_fetch.fetch", {"url": "https://example.com/index.html"}, ctx()
        )
        assert result.success
        assert result.data["status_code"] == 200
        assert result.data["body"] == "hello from the web"
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_post_with_body_via_mutate(self):
        """P0-002 — side-effecting verbs live on http_request.mutate, not fetch."""
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.read() == b"payload"
            return httpx.Response(201, json={"ok": True})

        connector = HttpFetchConnector(transport=mock_transport(handler))
        result = await connector.execute(
            "http_request.mutate",
            {"url": "https://example.com/submit", "method": "POST", "body": "payload"},
            ctx(),
        )
        assert result.success
        assert result.data["status_code"] == 201

    @pytest.mark.asyncio
    async def test_fetch_rejects_post(self):
        """P0-002 — the read-only tool must refuse a mutating method."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("fetch must not send POST")

        connector = HttpFetchConnector(transport=mock_transport(handler))
        result = await connector.execute(
            "http_fetch.fetch",
            {"url": "https://example.com/submit", "method": "POST", "body": "payload"},
            ctx(),
        )
        assert result.success is False
        assert result.error_code == "HTTP_METHOD_NOT_ALLOWED"

    @pytest.mark.asyncio
    async def test_mutate_rejects_get(self):
        """P0-002 — the mutating tool must refuse a read-only method."""
        connector = HttpFetchConnector(transport=mock_transport(ok_handler))
        result = await connector.execute(
            "http_request.mutate",
            {"url": "https://example.com/x", "method": "GET"},
            ctx(),
        )
        assert result.success is False
        assert result.error_code == "HTTP_METHOD_NOT_ALLOWED"

    @pytest.mark.asyncio
    async def test_list_tools_exposes_both(self):
        connector = HttpFetchConnector(transport=mock_transport(ok_handler))
        tools = await connector.list_tools()
        by_name = {t.name: t for t in tools}
        assert "http_fetch.fetch" in by_name
        assert "http_request.mutate" in by_name
        assert by_name["http_fetch.fetch"].risk_level == 1
        assert by_name["http_fetch.fetch"].side_effect is False
        assert by_name["http_request.mutate"].risk_level == 3
        assert by_name["http_request.mutate"].side_effect is True
        assert by_name["http_request.mutate"].external_write is True

    @pytest.mark.asyncio
    async def test_custom_headers_passed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("x-custom") == "yes"
            return httpx.Response(200, text="ok")

        connector = HttpFetchConnector(transport=mock_transport(handler))
        result = await connector.execute(
            "http_fetch.fetch",
            {"url": "https://example.com/h", "headers": {"x-custom": "yes"}},
            ctx(),
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_http_error_returns_fail(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        connector = HttpFetchConnector(transport=mock_transport(handler))
        result = await connector.execute("http_fetch.fetch", {"url": "https://example.com/x"}, ctx())
        assert result.success is False
        assert result.error_code == "HTTP_ERROR"


class TestTimeout:
    @pytest.mark.asyncio
    async def test_slow_handler_times_out(self):
        async def slow_handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.5)
            return httpx.Response(200, text="late")

        connector = HttpFetchConnector(transport=mock_transport(slow_handler))
        result = await connector.execute(
            "http_fetch.fetch", {"url": "https://example.com/slow", "timeout": 0.05}, ctx()
        )
        assert result.success is False
        assert result.error_code == "HTTP_TIMEOUT"


class TestSsrpProtection:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080/secret",
            "http://10.0.0.1/admin",
            "http://172.16.0.1/x",
            "http://172.31.255.255/x",
            "http://192.168.1.1/x",
            "http://localhost:8000/x",
            "http://0.0.0.0/x",
        ],
    )
    async def test_private_targets_blocked(self, url):
        connector = HttpFetchConnector(transport=mock_transport(ok_handler))
        result = await connector.execute("http_fetch.fetch", {"url": url}, ctx())
        assert result.success is False
        assert result.error_code == "SSRF_BLOCKED"

    @pytest.mark.asyncio
    async def test_allow_private_bypasses_guard(self):
        connector = HttpFetchConnector(
            transport=mock_transport(ok_handler), allow_private=True
        )
        result = await connector.execute(
            "http_fetch.fetch", {"url": "http://127.0.0.1:8080/secret"}, ctx()
        )
        assert result.success
        assert result.data["status_code"] == 200

    @pytest.mark.asyncio
    async def test_invalid_url(self):
        connector = HttpFetchConnector(transport=mock_transport(ok_handler))
        result = await connector.execute(
            "http_fetch.fetch", {"url": "ftp://example.com/x"}, ctx()
        )
        assert result.success is False
        assert result.error_code == "HTTP_URL_INVALID"


class TestDomainAllowlist:
    @pytest.mark.asyncio
    async def test_allowed_domain_passes(self):
        connector = HttpFetchConnector(
            transport=mock_transport(ok_handler), allowed_domains=["example.com"]
        )
        result = await connector.execute(
            "http_fetch.fetch", {"url": "https://example.com/x"}, ctx()
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_subdomain_passes(self):
        connector = HttpFetchConnector(
            transport=mock_transport(ok_handler), allowed_domains=["example.com"]
        )
        result = await connector.execute(
            "http_fetch.fetch", {"url": "https://sub.example.com/x"}, ctx()
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_disallowed_domain_blocked(self):
        connector = HttpFetchConnector(
            transport=mock_transport(ok_handler), allowed_domains=["example.com"]
        )
        result = await connector.execute("http_fetch.fetch", {"url": "https://evil.com/x"}, ctx())
        assert result.success is False
        assert result.error_code == "DOMAIN_NOT_ALLOWED"


class TestResponseSize:
    @pytest.mark.asyncio
    async def test_large_body_truncated(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="x" * 5000)

        connector = HttpFetchConnector(transport=mock_transport(handler), max_response_bytes=200)
        result = await connector.execute("http_fetch.fetch", {"url": "https://example.com/big"}, ctx())
        assert result.success
        assert result.truncated is True
        assert len(result.data["body"]) == 200
        assert len(result.text) == 200


def test_html_to_text_strips_markup():
    """HTML is converted to readable text (no script/style markup leaking)."""
    from connectors.http_fetch.connector import html_to_text

    raw = (
        '<html><head><style>.x{color:red}</style></head><body>'
        '<script>alert("x")</script>'
        '<h1>标题</h1><p>第一段 &amp; 内容</p><div>第二段</div>'
        '<ul><li>项目一</li><li>项目二</li></ul></body></html>'
    )
    text = html_to_text(raw)
    assert "script" not in text and "alert" not in text
    assert "style" not in text and "color:red" not in text
    assert "标题" in text and "第一段 & 内容" in text and "项目一" in text
    assert text.startswith("标题") or "标题" in text


def test_mixed_private_public_resolution_is_rebinding_signal(monkeypatch):
    """P1-012 — a hostname resolving to private AND public is blocked."""
    from connectors.http_fetch.connector import _resolves_to_mixed_private_public

    def mixed(host, port=None, *a, **k):
        return [(2, 1, 6, "", ("10.0.0.5", 0)), (2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("socket.getaddrinfo", mixed)
    assert _resolves_to_mixed_private_public("rebind.example") is True

    def only_public(host, port=None, *a, **k):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("socket.getaddrinfo", only_public)
    assert _resolves_to_mixed_private_public("rebind.example") is False

    def only_private(host, port=None, *a, **k):
        return [(2, 1, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr("socket.getaddrinfo", only_private)
    assert _resolves_to_mixed_private_public("rebind.example") is False  # covered by _resolve_private
