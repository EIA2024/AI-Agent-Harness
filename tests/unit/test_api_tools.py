"""Tool listing endpoint with an injected fake registry."""

from __future__ import annotations

import uuid

import pytest

from personal_ai_os.common.models import ToolDescriptor
from personal_ai_os.db.models import User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer

_TEST_KEY = "tools-test-key"


async def _ensure_user():
    async with session_scope() as s:
        u = User(username=f"tools-{uuid.uuid4().hex[:8]}", api_key=_TEST_KEY)
        s.add(u)
        await s.flush()
        return u


class FakeRegistry:
    def __init__(self):
        self._tools = [
            ToolDescriptor(
                name="filesystem.read",
                namespace="filesystem",
                description="Read a file",
                input_schema={"type": "object", "properties": {}},
            ),
            ToolDescriptor(
                name="web.search",
                namespace="web",
                description="Search the web",
                input_schema={},
                risk_level=1,
                side_effect=False,
            ),
        ]

    def list_tools(self):
        return list(self._tools)


class AsyncFakeRegistry(FakeRegistry):
    async def list_tools(self):
        return list(self._tools)


@pytest.mark.asyncio
async def test_list_tools(make_api):
    await _ensure_user()
    headers = {"X-API-Key": _TEST_KEY}
    async with make_api(services=ServiceContainer(tool_registry=FakeRegistry())) as ac:
        r = await ac.get("/v1/tools", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        by_name = {t["name"]: t for t in data}
        assert set(by_name) == {"filesystem.read", "web.search"}
        assert by_name["filesystem.read"]["risk_level"] == 0
        assert by_name["web.search"]["risk_level"] == 1
        assert by_name["filesystem.read"]["description"] == "Read a file"


@pytest.mark.asyncio
async def test_list_tools_async_registry(make_api):
    await _ensure_user()
    headers = {"X-API-Key": _TEST_KEY}
    async with make_api(services=ServiceContainer(tool_registry=AsyncFakeRegistry())) as ac:
        r = await ac.get("/v1/tools", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) == 2


@pytest.mark.asyncio
async def test_list_tools_empty_without_registry(make_api):
    await _ensure_user()
    headers = {"X-API-Key": _TEST_KEY}
    async with make_api(services=ServiceContainer()) as ac:
        r = await ac.get("/v1/tools", headers=headers)
        assert r.status_code == 200
        assert r.json() == []
