"""Policy engine — R0–R4 risk model + rule matching (dept 04 §2–§3).

Rule semantics
--------------

Two categories of rules are evaluated in order:

1. **Specific rules** — rules that constrain by ``tool_name_pattern`` /
   ``namespace`` (e.g. ``shell.*``, ``credential.*``). These act as *overrides*
   over the generic risk-band rules (dept 04 §3: "override even R0/R1 shell
   operations"), so a harmless shell command is still ``ask`` even though R0
   would normally be auto-allowed.
2. **Risk-band rules** — rules that constrain purely by ``risk_min``/``risk_max``
   (``r0-auto`` … ``r4-strict``).

Within each category rules are matched in ascending ``(priority, id)`` order
(priority 0 = highest precedence).

If no rule matches, the engine falls back to the default policy for the tool's
``risk_level``: R0–R1 → allow, R2 → allow, R3 → ask, R4 → ask (passkey).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Callable

from ..common.models import PolicyDecision, ToolDescriptor, ToolExecutionContext

ContextFn = Callable[[ToolDescriptor, dict, ToolExecutionContext], bool]

DEFAULT_RULES = [
    {"id": "r0-auto", "priority": 0, "description": "R0 tools always auto-approved",
     "risk_max": 0, "decision": "allow"},
    {"id": "r1-auto", "priority": 1, "description": "R1 read-only tools auto-approved",
     "risk_min": 1, "risk_max": 1, "decision": "allow"},
    {"id": "r2-auto-notify", "priority": 2, "description": "R2 local write auto-approved (notified)",
     "risk_min": 2, "risk_max": 2, "decision": "allow"},
    {"id": "r3-ask", "priority": 3, "description": "R3 external communication / state change requires approval",
     "risk_min": 3, "risk_max": 3, "decision": "ask"},
    {"id": "r4-strict", "priority": 4, "description": "R4 destructive / credential operation requires strict approval",
     "risk_min": 4, "risk_max": 4, "decision": "ask", "require_auth_method": "passkey"},
    {"id": "shell-ask", "priority": 10, "description": "Shell commands always require approval (even harmless ones)",
     "tool_name_pattern": "shell.*", "decision": "ask"},
    {"id": "credential-deny", "priority": 0, "description": "Credential operations are always denied without explicit policy",
     "tool_name_pattern": "credential.*", "decision": "deny"},
]


@dataclass
class PolicyRule:
    """A single policy rule. All specified conditions must match to fire."""

    id: str
    priority: int
    description: str
    tool_name_pattern: str | None = None
    risk_min: int | None = None
    risk_max: int | None = None
    namespace: str | None = None
    decision: str = "allow"
    override_risk: int | None = None
    require_auth_method: str | None = None
    context_fn: ContextFn | None = None

    @property
    def is_specific(self) -> bool:
        """Specific rules (name/namespace based) override risk-band rules."""
        return bool(self.tool_name_pattern or self.namespace)

    # -- matching ----------------------------------------------------------

    def _name_matches(self, tool: ToolDescriptor) -> bool:
        if not self.tool_name_pattern:
            return True
        candidates = [tool.name]
        if tool.namespace and not tool.name.startswith(tool.namespace):
            candidates.append(f"{tool.namespace}.{tool.name}")
        return any(fnmatch.fnmatch(c, self.tool_name_pattern) for c in candidates)

    def _risk_matches(self, tool: ToolDescriptor) -> bool:
        risk = tool.risk_level
        if self.risk_min is not None and risk < self.risk_min:
            return False
        if self.risk_max is not None and risk > self.risk_max:
            return False
        return True

    def matches(self, tool: ToolDescriptor, arguments: dict, context: ToolExecutionContext | None = None) -> bool:
        """Return True if every specified condition holds for the tool call."""
        if not self._name_matches(tool):
            return False
        if not self._risk_matches(tool):
            return False
        if self.namespace and tool.namespace != self.namespace:
            return False
        if self.context_fn is not None:
            if not self.context_fn(tool, arguments, context):
                return False
        return True


class PolicyEngine:
    """Evaluates a tool call against the rule set."""

    def __init__(self, rules: list[PolicyRule] | None = None):
        raw = rules if rules is not None else [PolicyRule(**r) for r in DEFAULT_RULES]
        specific = [r for r in raw if r.is_specific]
        bands = [r for r in raw if not r.is_specific]
        self._specific = sorted(specific, key=lambda r: (r.priority, r.id))
        self._bands = sorted(bands, key=lambda r: (r.priority, r.id))
        self.rules = sorted(raw, key=lambda r: (r.priority, r.id))

    # -- evaluation --------------------------------------------------------

    async def evaluate(
        self,
        tool: ToolDescriptor,
        arguments: dict,
        context: ToolExecutionContext,
    ) -> PolicyDecision:
        rule = self._first_match(self._specific, tool, arguments, context)
        if rule is None:
            rule = self._first_match(self._bands, tool, arguments, context)
        if rule is None:
            return self._fallback(tool.risk_level)
        return self._decision_from_rule(rule, tool)

    def _first_match(self, rules: list[PolicyRule], tool, arguments, context) -> PolicyRule | None:
        for rule in rules:
            if rule.matches(tool, arguments, context):
                return rule
        return None

    def _decision_from_rule(self, rule: PolicyRule, tool: ToolDescriptor) -> PolicyDecision:
        risk_level = rule.override_risk if rule.override_risk is not None else tool.risk_level
        constraints: dict = {}
        if rule.require_auth_method:
            constraints["require_auth_method"] = rule.require_auth_method
        if rule.decision == "ask" and risk_level >= 3:
            constraints.setdefault("require_auth_method", "passkey")
        return PolicyDecision(
            decision=rule.decision,  # type: ignore[arg-type]
            risk_level=risk_level,
            reasons=[f"{rule.id}: {rule.description}"],
            constraints=constraints,
            requires_approval_receipt=rule.decision == "ask",
        )

    @staticmethod
    def _fallback(risk: int) -> PolicyDecision:
        """Default policy when no rule matches (dept 04 §2)."""
        if risk <= 1:
            return PolicyDecision(
                decision="allow", risk_level=risk,
                reasons=[f"No rule matched; R{risk} read/compute is auto-allowed."],
            )
        if risk == 2:
            return PolicyDecision(
                decision="allow", risk_level=risk,
                reasons=["No rule matched; R2 local write is auto-allowed."],
            )
        if risk == 3:
            return PolicyDecision(
                decision="ask", risk_level=risk,
                reasons=["No rule matched; R3 external side-effect requires approval."],
                requires_approval_receipt=True,
            )
        return PolicyDecision(
            decision="ask", risk_level=risk,
            reasons=["No rule matched; R4 destructive/credential operation requires strict approval."],
            constraints={"require_auth_method": "passkey"},
            requires_approval_receipt=True,
        )
