"""Calculator connector: safe arithmetic expression evaluation.

The evaluator parses the expression with Python's ``ast`` and only allows:
numeric literals, arithmetic operators (+ - * / ** and unary +/-), parentheses,
and a fixed whitelist of pure-math functions (sin/cos/tan/sqrt/log/abs).

Everything else — attribute access, subscripts, non-whitelisted calls, string
constants, dunder names — is rejected. This makes payloads like
``__import__('os').system(...)`` fail safely.
"""

from __future__ import annotations

import ast
import math
import operator

from personal_ai_os.common.models import ToolDescriptor, ToolExecutionContext, ToolResult

# Pure functions with no side effects and no file/network access.
_ALLOWED_FUNCTIONS: dict[str, object] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "sqrt": math.sqrt,
    "log": math.log,
    "abs": abs,
}

_ALLOWED_BINOPS: dict[type[ast.operator], object] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

_ALLOWED_UNARYOPS: dict[type[ast.unaryop], object] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_MAX_NODES = 200       # guard against pathological nesting
_MAX_EXPONENT = 1000   # guard against 9**9**9 style blowups


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise ValueError("only numeric literals are allowed")
    if isinstance(node, ast.Name):
        raise ValueError(f"bare name {node.id!r} is not allowed")
    if isinstance(node, ast.Call):
        func = node.func
        if not isinstance(func, ast.Name) or func.id not in _ALLOWED_FUNCTIONS:
            raise ValueError("only whitelisted math functions are allowed")
        fn = _ALLOWED_FUNCTIONS[func.id]
        args = [_evaluate(a) for a in node.args]
        if node.keywords:
            raise ValueError("keyword arguments are not allowed")
        try:
            return float(fn(*args))
        except ValueError as exc:
            raise ValueError(f"math domain error in {func.id}()") from exc
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _ALLOWED_BINOPS:
            raise ValueError("unsupported operator")
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        op = _ALLOWED_BINOPS[type(node.op)]
        if isinstance(node.op, ast.Pow):
            if abs(right) > _MAX_EXPONENT:
                raise ValueError("exponent too large")
        try:
            return float(op(left, right))
        except ZeroDivisionError as exc:
            raise ValueError("division by zero") from exc
        except OverflowError as exc:
            raise ValueError("numeric overflow") from exc
    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _ALLOWED_UNARYOPS:
            raise ValueError("unsupported unary operator")
        return float(_ALLOWED_UNARYOPS[type(node.op)](_evaluate(node.operand)))
    raise ValueError(f"unsupported expression node: {type(node).__name__}")


def safe_evaluate(expression: str) -> float:
    """Evaluate a sanitized arithmetic expression. Raises ``ValueError`` on any
    disallowed construct, syntax error, or numeric error."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression must be a non-empty string")
    if "__" in expression:
        raise ValueError("dunder access is not allowed")
    # Many models/users write `^` for power (e.g. `4^2`). Python parses `^` as
    # XOR, so normalize it to `**` before parsing. Safe here because the
    # evaluator only accepts numeric literals and whitelisted operators — any
    # `^` in such an expression can only be intended as exponentiation.
    expression = expression.replace("^", "**")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid expression: {exc.msg}") from exc
    if sum(1 for _ in ast.walk(tree)) > _MAX_NODES:
        raise ValueError("expression too complex")
    return _evaluate(tree)


class CalculatorConnector:
    """Exposes the R0, side-effect-free, idempotent ``calculator.evaluate`` tool."""

    connector_name = "calculator"

    def __init__(self) -> None:
        self._tools = [self._build_descriptor()]

    def _build_descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name="calculator.evaluate",
            namespace="calculator",
            description=(
                "Evaluate a safe arithmetic expression and return the numeric result. "
                "Supports + - * / ** and ^ (both mean power), parentheses, and the "
                "math functions sin, cos, tan, sqrt, log, abs."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Arithmetic expression to evaluate"},
                },
                "required": ["expression"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "number"}, "expression": {"type": "string"}},
            },
            risk_level=0,
            side_effect=False,
            destructive=False,
            external_write=False,
            idempotent=True,
            timeout_seconds=5,
            retry_policy="none",
            tags=["math", "calculator", "arithmetic"],
        )

    async def list_tools(self) -> list[ToolDescriptor]:
        return list(self._tools)

    async def execute(self, tool: str, arguments: dict, ctx: ToolExecutionContext) -> ToolResult:
        expression = arguments.get("expression")
        try:
            value = safe_evaluate(expression)
        except ValueError as exc:
            return ToolResult.fail(
                error=f"Invalid expression: {exc}", error_code="CALCULATOR_ERROR",
            )
        return ToolResult.ok(
            data={"result": value, "expression": expression},
            text=f"{expression} = {value}",
        )
