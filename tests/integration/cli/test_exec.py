"""Integration tests: ``personal-ai exec`` end-to-end against the real app.

Uses a scripted model so the full chain (POST message → run → SSE stream →
decode → normalize → reduce → render) is real, but deterministic.
"""

from __future__ import annotations

import io
import json

import pytest

from personal_ai_os.cli.api.client import AsyncAPIClient
from personal_ai_os.cli.commands.exec import run_exec
from personal_ai_os.cli.output.exit_code import ExitCode
from tests.integration.cli.conftest import _tool_call


@pytest.mark.asyncio
async def test_exec_text_simple(cli_client):
    async with cli_client() as client:
        out, err = io.StringIO(), io.StringIO()
        code = await run_exec(
            client, prompt="hi", output_format="text", stdout=out, stderr=err
        )
    assert code == ExitCode.OK
    assert out.getvalue().strip() == "hello world"
    assert "hello world" not in err.getvalue()


@pytest.mark.asyncio
async def test_exec_json_summary(cli_client):
    async with cli_client() as client:
        out, err = io.StringIO(), io.StringIO()
        code = await run_exec(
            client, prompt="hi", output_format="json", stdout=out, stderr=err
        )
    assert code == ExitCode.OK
    summary = json.loads(out.getvalue())
    assert summary["status"] == "completed"
    assert summary["output"]["text"] == "hello world"


@pytest.mark.asyncio
async def test_exec_stream_json_lines(cli_client):
    async with cli_client() as client:
        out, err = io.StringIO(), io.StringIO()
        code = await run_exec(
            client, prompt="hi", output_format="stream-json", stdout=out, stderr=err
        )
    assert code == ExitCode.OK
    lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert all(isinstance(line, dict) and "v" in line and "type" in line for line in lines)
    assert lines[-1]["type"] == "run.completed"
    assert lines[-1]["result"]["text"] == "hello world"


@pytest.mark.asyncio
async def test_exec_uses_existing_session(cli_client):
    async with cli_client() as client:
        created = await client.create_session(channel="cli")
        out, err = io.StringIO(), io.StringIO()
        code = await run_exec(
            client,
            prompt="hi",
            session_id=created.id,
            output_format="text",
            stdout=out,
            stderr=err,
        )
    assert code == ExitCode.OK
    assert "[new session" not in err.getvalue()


@pytest.mark.asyncio
async def test_exec_approval_required_exits_4(cli_client):
    script = [
        {
            "tool_calls": _tool_call(
                "mail.send", {"to": "boss@x.com", "subject": "hi", "body": "hello"}
            )
        },
        {"content": "邮件已发送。"},
    ]
    async with cli_client(script) as client:
        out, err = io.StringIO(), io.StringIO()
        code = await run_exec(
            client, prompt="给老板发邮件", output_format="text", stdout=out, stderr=err
        )
    assert code == ExitCode.APPROVAL_REQUIRED
    assert out.getvalue() == ""  # never fabricate an answer
    assert "approval" in err.getvalue()


@pytest.mark.asyncio
async def test_exec_approval_stream_json_ends_with_required(cli_client):
    script = [
        {
            "tool_calls": _tool_call(
                "mail.send", {"to": "boss@x.com", "subject": "hi", "body": "hello"}
            )
        }
    ]
    async with cli_client(script) as client:
        out, err = io.StringIO(), io.StringIO()
        code = await run_exec(
            client, prompt="发邮件", output_format="stream-json", stdout=out, stderr=err
        )
    assert code == ExitCode.APPROVAL_REQUIRED
    lines = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert lines[-1]["type"] == "approval.required"


@pytest.mark.asyncio
async def test_exec_network_error_exits_7():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = AsyncAPIClient(
        base_url="http://unreachable",
        api_key="k",
        transport=httpx.MockTransport(handler),
    )
    out, err = io.StringIO(), io.StringIO()
    code = await run_exec(client, prompt="hi", output_format="text", stdout=out, stderr=err)
    assert code == ExitCode.NETWORK_UNAVAILABLE
    assert "error" in err.getvalue()


@pytest.mark.asyncio
async def test_exec_unsupported_format_usage_error(cli_client):
    async with cli_client() as client:
        out, err = io.StringIO(), io.StringIO()
        code = await run_exec(
            client, prompt="hi", output_format="yaml", stdout=out, stderr=err
        )
    assert code == ExitCode.USAGE_OR_CONFIG
