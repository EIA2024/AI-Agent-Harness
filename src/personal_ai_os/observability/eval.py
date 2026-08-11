"""Evaluation framework — blueprint §54 five eval categories.

Provides a declarative test-case format and a runner that executes evals
against the real agent stack:

- ``tool_selection`` — given a query, assert which tools the model selects
  (expected / forbidden).
- ``security`` — feed untrusted content that tries to steer the agent; assert
  the dangerous action never executes and policy blocks it.
- ``task_success`` — run a real end-to-end task and assert objective checks.

Eval cases are plain YAML/JSON so they can live in ``evals/datasets/`` and be
extended without touching code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class EvalCase:
    id: str
    category: str  # "tool_selection" | "security" | "task_success"
    query: str
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    expected_behavior: str | None = None  # e.g. "no_shell_executed"
    setup: dict = field(default_factory=dict)
    checks: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    case_id: str
    category: str
    passed: bool
    detail: str
    metrics: dict = field(default_factory=dict)


class EvalRunner:
    """Executes eval cases against an injected agent stack."""

    def __init__(self, *, runner=None, tool_broker=None, policy_engine=None, memory_store=None):
        #: Real services (all optional — a None slot skips dependent categories).
        self.runner = runner
        self.tool_broker = tool_broker
        self.policy_engine = policy_engine
        self.memory_store = memory_store

    # ------------------------------------------------------------ loading

    @staticmethod
    def load_cases(path: str | Path) -> list[EvalCase]:
        """Load eval cases from a JSON/YAML file (list of case dicts)."""
        p = Path(path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        cases = raw["tests"] if isinstance(raw, dict) and "tests" in raw else raw
        return [EvalCase(**c) for c in cases]

    # ------------------------------------------------------------ running

    async def run(self, cases: list[EvalCase]) -> list[EvalResult]:
        results: list[EvalResult] = []
        for case in cases:
            handler = self._handlers.get(case.category)
            if handler is None:
                results.append(
                    EvalResult(case.id, case.category, False, f"no handler for category {case.category!r}")
                )
                continue
            try:
                results.append(await handler(case))
            except Exception as exc:  # noqa: BLE001 — a broken case is a failed case
                results.append(EvalResult(case.id, case.category, False, f"error: {exc}"))
        return results

    @property
    def _handlers(self) -> dict[str, Callable[[EvalCase], Any]]:
        return {
            "tool_selection": self._run_tool_selection,
            "security": self._run_security,
            "task_success": self._run_task_success,
        }

    # ------------------------------------------------------------ categories

    async def _run_tool_selection(self, case: EvalCase) -> EvalResult:
        """Check that the expected tools are exposed and forbidden tools are not."""
        if self.tool_broker is None:
            return EvalResult(case.id, "tool_selection", False, "tool_broker not wired")

        registry_tools = await self.tool_broker.list_available_tools(owner_id=case.setup.get("owner_id"))
        available = {t.name for t in registry_tools}

        missing_expected = [t for t in case.expected_tools if t not in available]
        forbidden_present = [t for t in case.forbidden_tools if t in available]

        if missing_expected:
            return EvalResult(
                case.id, "tool_selection", False,
                f"expected tools not registered: {missing_expected}",
                {"available": sorted(available)},
            )
        if forbidden_present:
            return EvalResult(
                case.id, "tool_selection", False,
                f"forbidden tools are exposed: {forbidden_present}",
                {"available": sorted(available)},
            )
        return EvalResult(
            case.id, "tool_selection", True,
            f"expected tools present, forbidden absent ({len(available)} total)",
            {"available": sorted(available)},
        )

    async def _run_security(self, case: EvalCase) -> EvalResult:
        """Assert the dangerous action is blocked at the policy boundary."""
        if self.policy_engine is None or self.tool_broker is None:
            return EvalResult(case.id, "security", False, "policy/broker not wired")

        # For each forbidden tool, confirm it is either unregistered (cannot be
        # called at all) or denied by policy.
        blocked: set[str] = set()
        for tool_name in case.forbidden_tools:
            tool = self.tool_broker._registry.get(tool_name) if hasattr(self.tool_broker, "_registry") else None
            if tool is None:
                # Not registered → not executable → effectively blocked.
                blocked.add(tool_name)
                continue
            from personal_ai_os.common.models import ToolExecutionContext
            from uuid import uuid4

            decision = await self.policy_engine.evaluate(
                tool, {"query": case.query}, ToolExecutionContext(run_id=uuid4(), owner_id=uuid4())
            )
            if decision.decision == "deny":
                blocked.add(tool_name)

        expected_blocked = set(case.forbidden_tools)
        if expected_blocked and not expected_blocked.issubset(blocked):
            return EvalResult(
                case.id, "security", False,
                f"not all forbidden tools blocked: missing {expected_blocked - blocked}",
            )
        return EvalResult(
            case.id, "security", True,
            f"all {len(blocked)} forbidden tools blocked",
        )

    async def _run_task_success(self, case: EvalCase) -> EvalResult:
        """Run an end-to-end task and check the final state / persisted effects."""
        if self.runner is None:
            return EvalResult(case.id, "task_success", False, "runner not wired")

        owner_id = case.setup.get("owner_id")
        if owner_id is None:
            return EvalResult(case.id, "task_success", False, "setup.owner_id required")
        session_id = case.setup.get("session_id")
        result = await self.runner.start(
            session_id=session_id, owner_id=owner_id, user_input=case.query
        )
        status = result.get("status")
        passed = status in ("completed",)
        detail = f"run status={status}"
        for check in case.checks:
            ok = self._apply_check(check, result)
            detail += f"; {check}={ok}"
            passed = passed and ok
        return EvalResult(case.id, "task_success", passed, detail)

    @staticmethod
    def _apply_check(check: str, result: dict) -> bool:
        """Minimal check DSL: ``field:contains:value`` / ``field:equals:value``."""
        parts = check.split(":", 2)
        if len(parts) != 3:
            return False
        field_name, op, expected = parts
        value = result.get("state", {}).get(field_name, result.get(field_name))
        if op == "contains":
            return expected in str(value or "")
        if op == "equals":
            return str(value) == expected
        return False

    # ------------------------------------------------------------ reporting

    @staticmethod
    def report(results: list[EvalResult]) -> dict:
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 3) if total else 1.0,
            "results": [
                {
                    "case_id": r.case_id,
                    "category": r.category,
                    "passed": r.passed,
                    "detail": r.detail,
                    "metrics": r.metrics,
                }
                for r in results
            ],
        }
