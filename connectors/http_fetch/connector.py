"""HTTP Fetch connector: single URL fetcher with SSRF protection.

- Default request timeout 10s (argument ``timeout``, capped at 30s).
- Response size capped at ``max_response_bytes`` (default 2 MiB); oversized
  bodies are truncated and flagged.
- Optional domain allowlist (empty = allow all but log a warning).
- SSRF guard: refuses hosts that resolve to private/loopback/link-local
  addresses unless ``allow_private=True``.
- Redirects are not followed (keeps SSRF protection meaningful).

Tests inject an ``httpx.MockTransport`` via the ``transport`` constructor arg
so no real network traffic is ever emitted.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import ipaddress
import logging
import re
import socket
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from personal_ai_os.common.models import ToolDescriptor, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
MAX_TIMEOUT = 30.0
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MiB


def _is_private_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_unspecified
        or addr.is_link_local
        or addr.is_reserved
    )


def _resolve_private(host: str) -> bool:
    """True if ``host`` is localhost, an IP literal, or resolves to a private IP.

    Resolution failure (no DNS) is treated as *not* private — the subsequent HTTP
    connect will fail anyway, and tests rely on MockTransport without DNS.
    """
    if host.lower() == "localhost":
        return True
    # IP literal → no DNS needed.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return _is_private_ip(host)
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        if _is_private_ip(info[4][0]):
            return True
    return False


def _resolves_to_mixed_private_public(host: str) -> bool:
    """True when a hostname resolves to BOTH private and public addresses.

    P1-012: a mix is a strong DNS-rebinding signal (the name answers privately
    to us and publicly to the upstream) — block rather than let a single
    resolution-time check be raced.
    """
    if host.lower() == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False  # an IP literal is a single, pinned address
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    seen_private = any(_is_private_ip(info[4][0]) for info in infos)
    seen_public = any(not _is_private_ip(info[4][0]) for info in infos)
    return seen_private and seen_public


class _TextExtractor(HTMLParser):
    """Pulls readable text out of HTML, dropping script/style/noscript content."""

    _BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "pre", "blockquote"}
    _SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP_TAGS:
            self._skip += 1
        if tag in self._BLOCK_TAGS and self._skip == 0:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self.parts.append(data)


def html_to_text(raw: str, max_chars: int = 8000) -> str:
    """Convert HTML to condensed plain text (for LLM context).

    Strips script/style/noscript, block-level tags, HTML entities and collapses
    whitespace, so a web page becomes readable prose instead of raw markup.
    """
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # noqa: BLE001 - best-effort on malformed HTML
        pass
    text = html_lib.unescape("".join(parser.parts))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = text.strip()
    return text[:max_chars]


class HttpFetchConnector:
    """HTTP tools: read-only ``http_fetch.fetch`` + mutating ``http_request.mutate``.

    P0-002: side-effecting HTTP verbs must NOT ride on a read-only descriptor.
    ``fetch`` is GET/HEAD only (R1, auto-allowed); POST/PUT/PATCH/DELETE live on
    ``http_request.mutate`` (R3 → requires approval), so an agent can never
    silently write/delete to an external service without going through policy.
    """

    connector_name = "http_fetch"

    _READ_METHODS = ("GET", "HEAD")
    _MUTATE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

    def __init__(
        self,
        *,
        allowed_domains: list[str] | None = None,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        default_timeout: float = DEFAULT_TIMEOUT,
        allow_private: bool = False,
        transport: Any = None,
    ) -> None:
        self.allowed_domains = [d.lower().lstrip(".") for d in (allowed_domains or [])]
        self.max_response_bytes = max_response_bytes
        self.default_timeout = default_timeout
        self.allow_private = allow_private
        self._transport = transport

    def _read_descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name="http_fetch.fetch",
            namespace="http_fetch",
            description=(
                "Fetch a URL with a read-only HTTP method (GET/HEAD) and return status + body text. "
                "Redirects are not followed; private/loopback addresses are blocked. "
                "For POST/PUT/PATCH/DELETE use http_request.mutate."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute http(s) URL to fetch"},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "HEAD"],
                        "default": "GET",
                        "description": "Read-only methods only",
                    },
                    "headers": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "default": {},
                    },
                    "timeout": {"type": "number", "minimum": 0.1, "maximum": MAX_TIMEOUT, "default": DEFAULT_TIMEOUT},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status_code": {"type": "integer"},
                    "body": {"type": "string"},
                    "url": {"type": "string"},
                    "elapsed_ms": {"type": "number"},
                },
            },
            risk_level=1,
            side_effect=False,
            destructive=False,
            external_write=False,
            idempotent=True,
            timeout_seconds=int(MAX_TIMEOUT),
            retry_policy="once",
            tags=["http", "web", "fetch", "read"],
            result_trust="untrusted_web",
        )

    def _mutate_descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name="http_request.mutate",
            namespace="http_request",
            description=(
                "Send a side-effecting HTTP request (POST/PUT/PATCH/DELETE) to a URL. "
                "Requires approval (R3); use http_fetch.fetch for read-only GET/HEAD."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute http(s) URL"},
                    "method": {
                        "type": "string",
                        "enum": ["POST", "PUT", "PATCH", "DELETE"],
                        "default": "POST",
                    },
                    "headers": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "default": {},
                    },
                    "body": {"type": "string", "default": None},
                    "timeout": {"type": "number", "minimum": 0.1, "maximum": MAX_TIMEOUT, "default": DEFAULT_TIMEOUT},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            risk_level=3,
            side_effect=True,
            destructive=True,  # DELETE is in the method set
            external_write=True,
            idempotent=False,
            timeout_seconds=int(MAX_TIMEOUT),
            retry_policy="none",
            tags=["http", "web", "write"],
            result_trust="untrusted_web",
        )

    async def list_tools(self) -> list[ToolDescriptor]:
        return [self._read_descriptor(), self._mutate_descriptor()]

    # ------------------------------------------------------------------
    # Security checks
    # ------------------------------------------------------------------

    def _host_allowed(self, host: str) -> bool:
        if not self.allowed_domains:
            return True
        host = host.lower()
        for domain in self.allowed_domains:
            if host == domain or host.endswith("." + domain):
                return True
        return False

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self, tool: str, arguments: dict, ctx: ToolExecutionContext) -> ToolResult:
        url = arguments.get("url")
        parsed = urlparse(url)
        host = parsed.hostname
        if parsed.scheme not in ("http", "https") or not host:
            return ToolResult.fail(error=f"invalid URL: {url!r}", error_code="HTTP_URL_INVALID")

        if not self._host_allowed(host):
            logger.warning("http_fetch: domain %r not in allowlist", host)
            return ToolResult.fail(
                error=f"domain {host!r} is not in the allowlist", error_code="DOMAIN_NOT_ALLOWED",
            )
        if not self.allow_private and _resolve_private(host):
            logger.warning("http_fetch: blocking SSRF target %r", host)
            return ToolResult.fail(
                error=f"refusing to fetch private/loopback address: {host}", error_code="SSRF_BLOCKED",
            )
        if not self.allow_private and _resolves_to_mixed_private_public(host):
            # P1-012: DNS-rebinding signal — resolves to private AND public.
            logger.warning("http_fetch: blocking mixed-resolution host %r (DNS rebinding?)", host)
            return ToolResult.fail(
                error=f"refusing to fetch host with mixed private/public resolution: {host}",
                error_code="SSRF_BLOCKED",
            )

        short = tool.split(".")[-1] if "." in tool else tool
        method = str(arguments.get("method", "GET")).upper()
        if short == "fetch":
            if method not in self._READ_METHODS:
                return ToolResult.fail(
                    error=f"http_fetch.fetch is read-only; method {method} not allowed — use http_request.mutate",
                    error_code="HTTP_METHOD_NOT_ALLOWED",
                )
        elif short == "mutate":
            if method not in self._MUTATE_METHODS:
                return ToolResult.fail(
                    error=f"http_request.mutate requires a mutating method; got {method}",
                    error_code="HTTP_METHOD_NOT_ALLOWED",
                )
        else:
            return ToolResult.fail(error=f"unknown http tool: {tool}", error_code="UNKNOWN_TOOL")

        headers = arguments.get("headers") or {}
        body = arguments.get("body")
        timeout = min(float(arguments.get("timeout", self.default_timeout)), MAX_TIMEOUT)

        client_kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout),
            "follow_redirects": False,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                try:
                    async with asyncio.timeout(timeout):
                        response = await client.request(method, url, headers=headers, content=body)
                except TimeoutError:
                    return ToolResult.fail(
                        error=f"request to {url!r} timed out after {timeout}s", error_code="HTTP_TIMEOUT",
                    )
                except httpx.HTTPError as exc:
                    return ToolResult.fail(error=f"HTTP error: {exc}", error_code="HTTP_ERROR")

                body_bytes, truncated, raw_size = await self._read_bounded(response)
        except httpx.HTTPError as exc:
            return ToolResult.fail(error=f"HTTP error: {exc}", error_code="HTTP_ERROR")

        text = body_bytes.decode(response.charset_encoding or "utf-8", errors="replace")
        # Convert HTML to readable text so the LLM gets useful content instead of
        # raw <script>/<style> markup (which makes reasoning models loop forever).
        content_type = (response.headers.get("content-type") or "").lower()
        if "html" in content_type:
            text = html_to_text(text)
        text = text[: self.max_response_bytes] if truncated else text
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ToolResult(
            success=True,
            data={
                "url": url,
                "status_code": response.status_code,
                "body": text,
                "headers": {k: v for k, v in list(response.headers.items())[:20]},
                "elapsed_ms": latency_ms,
            },
            text=text,
            latency_ms=latency_ms,
            truncated=truncated,
            raw_size_bytes=raw_size,
        )

    async def _read_bounded(self, response: httpx.Response) -> tuple[bytes, bool, int]:
        """Read the response body up to ``max_response_bytes``, flagging truncation."""
        chunks: list[bytes] = []
        size = 0
        truncated = False
        async for chunk in response.aiter_bytes():
            remaining = self.max_response_bytes - size
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                size += remaining
                truncated = True
                break
            chunks.append(chunk)
            size += len(chunk)
        raw_size = int(response.headers.get("content-length") or size)
        return b"".join(chunks), truncated, raw_size
