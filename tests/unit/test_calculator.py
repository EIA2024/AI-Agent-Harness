"""Unit tests for the calculator connector (safe expression evaluation)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from connectors.calculator.connector import CalculatorConnector, safe_evaluate
from personal_ai_os.common.models import ToolExecutionContext


def ctx() -> ToolExecutionContext:
    return ToolExecutionContext(run_id=uuid4(), owner_id=uuid4())


@pytest.fixture
def connector() -> CalculatorConnector:
    return CalculatorConnector()


@pytest.mark.asyncio
async def test_list_tools(connector):
    tools = await connector.list_tools()
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "calculator.evaluate"
    assert tool.namespace == "calculator"
    assert tool.risk_level == 0
    assert tool.idempotent is True
    assert "expression" in tool.input_schema["properties"]
    assert "expression" in tool.input_schema["required"]


@pytest.mark.asyncio
async def test_evaluate_basic_arithmetic(connector):
    result = await connector.execute("calculator.evaluate", {"expression": "2+2"}, ctx())
    assert result.success
    assert result.data["result"] == 4

    result = await connector.execute("calculator.evaluate", {"expression": "(1+2)*3"}, ctx())
    assert result.success
    assert result.data["result"] == 9


@pytest.mark.asyncio
async def test_evaluate_division(connector):
    result = await connector.execute("calculator.evaluate", {"expression": "10/3"}, ctx())
    assert result.success
    assert result.data["result"] == pytest.approx(3.333, rel=1e-3)


@pytest.mark.asyncio
async def test_evaluate_power(connector):
    result = await connector.execute("calculator.evaluate", {"expression": "2**10"}, ctx())
    assert result.success
    assert result.data["result"] == 1024


@pytest.mark.asyncio
async def test_evaluate_math_functions(connector):
    assert (await connector.execute("calculator.evaluate", {"expression": "sin(0)"}, ctx())).data["result"] == 0
    assert (await connector.execute("calculator.evaluate", {"expression": "sqrt(16)"}, ctx())).data["result"] == 4
    assert (await connector.execute("calculator.evaluate", {"expression": "abs(-3)"}, ctx())).data["result"] == 3


@pytest.mark.asyncio
async def test_evaluate_unary_and_float(connector):
    assert (await connector.execute("calculator.evaluate", {"expression": "-5"}, ctx())).data["result"] == -5
    result = await connector.execute("calculator.evaluate", {"expression": "0.5 * 2"}, ctx())
    assert result.data["result"] == 1.0


class TestInjectionAttacks:
    @pytest.mark.asyncio
    async def test_import_os_system(self, connector):
        result = await connector.execute(
            "calculator.evaluate", {"expression": "__import__('os').system('x')"}, ctx()
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_dunder_rejected_directly(self, connector):
        result = await connector.execute("calculator.evaluate", {"expression": "__class__"}, ctx())
        assert result.success is False

    @pytest.mark.asyncio
    async def test_attribute_access_rejected(self, connector):
        for payload in [
            "().__class__",
            "1 .__class__",
            "['a'].__class__",
            "(1).real",
            "open('x')",
            "'x'.upper()",
        ]:
            result = await connector.execute("calculator.evaluate", {"expression": payload}, ctx())
            assert result.success is False, f"payload should fail: {payload}"

    @pytest.mark.asyncio
    async def test_subscript_rejected(self, connector):
        result = await connector.execute("calculator.evaluate", {"expression": "a[0]"}, ctx())
        assert result.success is False

    @pytest.mark.asyncio
    async def test_string_constant_rejected(self, connector):
        result = await connector.execute("calculator.evaluate", {"expression": "'hello'"}, ctx())
        assert result.success is False


@pytest.mark.asyncio
async def test_division_by_zero_fails(connector):
    result = await connector.execute("calculator.evaluate", {"expression": "1/0"}, ctx())
    assert result.success is False
    assert "division by zero" in result.error


@pytest.mark.asyncio
async def test_math_domain_error_fails(connector):
    result = await connector.execute("calculator.evaluate", {"expression": "sqrt(-1)"}, ctx())
    assert result.success is False


@pytest.mark.asyncio
async def test_too_large_exponent_fails(connector):
    result = await connector.execute("calculator.evaluate", {"expression": "9**9**9"}, ctx())
    assert result.success is False


@pytest.mark.asyncio
async def test_missing_expression_fails(connector):
    result = await connector.execute("calculator.evaluate", {}, ctx())
    assert result.success is False


def test_safe_evaluate_raises_value_error_on_attack():
    with pytest.raises(ValueError):
        safe_evaluate("__import__('os').system('x')")
    with pytest.raises(ValueError):
        safe_evaluate("1; import os")


def test_power_with_caret():
    """`^` is a common power convention (models write `4^2`); it must work."""
    from connectors.calculator.connector import safe_evaluate

    assert safe_evaluate("4^2") == 16.0
    assert safe_evaluate("2 ^ 3") == 8.0
    assert safe_evaluate("sqrt(4^2 - 3^2)/4") == pytest.approx(0.6614378277661477)
    assert safe_evaluate("2 ** 3") == 8.0  # Python-native power still works
