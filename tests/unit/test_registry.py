"""Unit tests for ToolRegistry."""

from __future__ import annotations

import threading

import pytest

from personal_ai_os.common.models import ToolDescriptor
from personal_ai_os.tool_broker.registry import ToolRegistry


def make_tool(name: str, namespace: str, *, description: str = "A tool", tags=None, risk_level: int = 0) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        namespace=namespace,
        description=description,
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        risk_level=risk_level,
        tags=tags or [],
    )


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(make_tool("filesystem.read", "filesystem", description="Read a file from disk", tags=["read", "file"], risk_level=1))
    reg.register(make_tool("filesystem.write", "filesystem", description="Write a file to disk", tags=["write", "file"], risk_level=2))
    reg.register(make_tool("calculator.evaluate", "calculator", description="Evaluate arithmetic", tags=["math"], risk_level=0))
    reg.register(make_tool("http_fetch.fetch", "http_fetch", description="Fetch a URL", tags=["web"], risk_level=1))
    return reg


class TestRegisterAndGet:
    def test_register_and_get(self, registry):
        tool = registry.get("filesystem.read")
        assert tool is not None
        assert tool.namespace == "filesystem"
        assert tool.risk_level == 1

    def test_get_missing_returns_none(self, registry):
        assert registry.get("nope.missing") is None

    def test_register_is_idempotent_overwrite(self, registry, caplog):
        updated = make_tool("filesystem.read", "filesystem", description="Updated description")
        registry.register(updated)  # should not raise
        tool = registry.get("filesystem.read")
        assert tool.description == "Updated description"
        assert any("already registered" in r.message for r in caplog.records)

    def test_unregister(self, registry):
        registry.unregister("calculator.evaluate")
        assert registry.get("calculator.evaluate") is None
        # unregister of a missing tool is a no-op
        registry.unregister("calculator.evaluate")

    def test_list_all(self, registry):
        names = {t.name for t in registry.list_all()}
        assert names == {
            "filesystem.read",
            "filesystem.write",
            "calculator.evaluate",
            "http_fetch.fetch",
        }

    def test_list_by_namespace(self, registry):
        tools = registry.list_by_namespace("filesystem")
        assert {t.name for t in tools} == {"filesystem.read", "filesystem.write"}
        assert registry.list_by_namespace("does_not_exist") == []


class TestSearch:
    def test_search_by_description_keyword(self, registry):
        results = registry.search("arithmetic")
        assert [t.name for t in results] == ["calculator.evaluate"]

    def test_search_by_tag(self, registry):
        results = registry.search("math")
        assert [t.name for t in results] == ["calculator.evaluate"]

    def test_search_by_name(self, registry):
        results = registry.search("filesystem.read")
        assert "filesystem.read" in {t.name for t in results}

    def test_search_namespace_prefix(self, registry):
        results = registry.search("filesystem")
        assert {t.name for t in results} == {"filesystem.read", "filesystem.write"}

    def test_search_empty_query_returns_risk_bounded(self, registry):
        # all risk_level <= 4 -> everything
        results = registry.search("")
        assert len(results) == 4

    def test_search_risk_max_filters(self, registry):
        results = registry.search("file", risk_max=1)
        names = {t.name for t in results}
        assert names == {"filesystem.read"}
        assert "filesystem.write" not in names  # risk 2 > 1

    def test_search_limit(self, registry):
        results = registry.search("", limit=2)
        assert len(results) == 2

    def test_search_case_insensitive(self, registry):
        assert [t.name for t in registry.search("ARITHMETIC")] == ["calculator.evaluate"]

    def test_search_no_match(self, registry):
        assert registry.search("zzzzz-nothing") == []


class TestListForLlm:
    def test_list_for_llm_schema_shape(self, registry):
        schemas = registry.list_for_llm()
        assert len(schemas) == 4
        first = schemas[0]
        assert first["type"] == "function"
        assert "name" in first["function"]
        assert "description" in first["function"]
        assert "parameters" in first["function"]

    def test_list_for_llm_with_query(self, registry):
        schemas = registry.list_for_llm(query="read")
        names = {s["function"]["name"] for s in schemas}
        assert names == {"filesystem.read"}

    def test_list_for_llm_risk_max(self, registry):
        schemas = registry.list_for_llm(risk_max=1)
        names = {s["function"]["name"] for s in schemas}
        assert "filesystem.write" not in names

    def test_list_for_llm_namespace_filter(self, registry):
        schemas = registry.list_for_llm(namespace_filter=["calculator"])
        names = {s["function"]["name"] for s in schemas}
        assert names == {"calculator.evaluate"}


class TestRegisterConnector:
    class _FakeConnector:
        connector_name = "fake"

        def __init__(self, tools):
            self._tools = tools

        async def list_tools(self):
            return self._tools

    @pytest.mark.asyncio
    async def test_register_connector_registers_all_tools(self):
        reg = ToolRegistry()
        connector = self._FakeConnector([make_tool("fake.a", "fake"), make_tool("fake.b", "fake")])
        await reg.register_connector(connector)
        assert reg.get("fake.a") is not None
        assert reg.get("fake.b") is not None
        assert len(reg.list_by_namespace("fake")) == 2


class TestThreadSafety:
    def test_concurrent_registration(self):
        reg = ToolRegistry()

        def worker(worker_id: int) -> None:
            for j in range(50):
                reg.register(make_tool(f"t.{worker_id}.{j}", "t", description=f"tool {worker_id}.{j}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(reg.list_all()) == 500
        # spot check a registered tool survives
        assert reg.get("t.7.42") is not None

    def test_concurrent_read_write(self):
        reg = ToolRegistry()
        for i in range(50):
            reg.register(make_tool(f"base.{i}", "base"))
        errors: list[Exception] = []

        def reader() -> None:
            try:
                for _ in range(200):
                    reg.list_all()
                    reg.search("base")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def writer() -> None:
            try:
                for i in range(100):
                    reg.register(make_tool(f"w.{i}", "w"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(4)] + [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(reg.list_all()) == 50 + 100
