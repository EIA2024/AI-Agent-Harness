"""Tests for the policy engine: R0-R4 defaults, overrides and fallback."""

from __future__ import annotations

from uuid import uuid4

import pytest

from personal_ai_os.common.models import ToolDescriptor, ToolExecutionContext
from personal_ai_os.policy_engine import DEFAULT_RULES, PolicyEngine, PolicyRule


def tool(name="calc", namespace="core", risk=0, **kw) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        namespace=namespace,
        description=f"{namespace}.{name}",
        input_schema={"type": "object"},
        risk_level=risk,
        **kw,
    )


def ctx() -> ToolExecutionContext:
    return ToolExecutionContext(run_id=uuid4(), owner_id=uuid4())


@pytest.fixture
def engine() -> PolicyEngine:
    return PolicyEngine()


async def test_r0_allow(engine):
    decision = await engine.evaluate(tool(name="calc", risk=0), {"expression": "1+1"}, ctx())
    assert decision.decision == "allow"
    assert decision.risk_level == 0
    assert decision.requires_approval_receipt is False


async def test_r1_allow(engine):
    decision = await engine.evaluate(tool(name="fs.read", risk=1), {"path": "/tmp/a"}, ctx())
    assert decision.decision == "allow"
    assert decision.risk_level == 1


async def test_r2_allow(engine):
    decision = await engine.evaluate(tool(name="fs.write", risk=2), {"path": "/tmp/a"}, ctx())
    assert decision.decision == "allow"
    assert decision.risk_level == 2
    assert decision.requires_approval_receipt is False


async def test_r3_ask(engine):
    decision = await engine.evaluate(
        tool(name="email.send", risk=3), {"to": "external@x.com"}, ctx()
    )
    assert decision.decision == "ask"
    assert decision.risk_level == 3
    assert decision.requires_approval_receipt is True


async def test_r4_ask_with_auth(engine):
    decision = await engine.evaluate(
        tool(name="fs.delete", risk=4), {"path": "/"}, ctx()
    )
    assert decision.decision == "ask"
    assert decision.risk_level == 4
    assert decision.requires_approval_receipt is True
    assert decision.constraints.get("require_auth_method") == "passkey"


async def test_shell_ask_overrides_even_harmless(engine):
    # Shell is R4-normally risk 0, but the shell.* rule forces ask.
    decision = await engine.evaluate(tool(name="shell.run", namespace="shell", risk=0), {"command": "ls"}, ctx())
    assert decision.decision == "ask"


async def test_credential_deny_overrides(engine):
    decision = await engine.evaluate(
        tool(name="credential.get", namespace="credential", risk=3), {"key": "github"}, ctx()
    )
    assert decision.decision == "deny"
    assert decision.requires_approval_receipt is False


async def test_rule_priority_ties_resolved_by_specific_over_band():
    # A name-specific rule beats a risk-band rule even at lower priority.
    rules = [
        PolicyRule(id="band", priority=0, description="any risk<=0 allow", risk_max=0, decision="allow"),
        PolicyRule(id="specific", priority=10, description="shell ask", tool_name_pattern="shell.*", decision="ask"),
    ]
    engine = PolicyEngine(rules=rules)
    decision = await engine.evaluate(tool(name="shell.run", risk=0), {}, ctx())
    assert decision.decision == "ask"


async def test_no_rule_fallback_by_risk():
    # Engine with no matching rules -> default policy by tool.risk_level.
    engine = PolicyEngine(rules=[])

    d0 = await engine.evaluate(tool(name="anything", risk=0), {}, ctx())
    assert d0.decision == "allow"

    d2 = await engine.evaluate(tool(name="anything", risk=2), {}, ctx())
    assert d2.decision == "allow"

    d3 = await engine.evaluate(tool(name="anything", risk=3), {}, ctx())
    assert d3.decision == "ask"
    assert d3.requires_approval_receipt is True

    d4 = await engine.evaluate(tool(name="anything", risk=4), {}, ctx())
    assert d4.decision == "ask"
    assert d4.constraints.get("require_auth_method") == "passkey"


async def test_override_risk():
    rules = [
        PolicyRule(id="bump", priority=0, description="bump", tool_name_pattern="email.*",
                   risk_min=0, decision="ask", override_risk=3),
    ]
    engine = PolicyEngine(rules=rules)
    decision = await engine.evaluate(tool(name="email.send", risk=1), {}, ctx())
    assert decision.decision == "ask"
    assert decision.risk_level == 3  # overridden from 1 -> 3


async def test_context_fn_argument_conditions():
    def external_recipient(tool_, arguments, context):
        return "external" in str(arguments.get("to", ""))

    rules = [
        PolicyRule(id="email-external", priority=0, description="external email needs approval",
                   tool_name_pattern="email.*", decision="ask", context_fn=external_recipient),
    ]
    engine = PolicyEngine(rules=rules)

    external = await engine.evaluate(tool(name="email.send", risk=2), {"to": "external@x.com"}, ctx())
    assert external.decision == "ask"

    self_mail = await engine.evaluate(tool(name="email.send", risk=2), {"to": "me@home"}, ctx())
    assert self_mail.decision == "allow"  # no rule matched -> R2 allow


def test_default_rules_are_well_formed():
    assert len(DEFAULT_RULES) == 7
    ids = [r["id"] for r in DEFAULT_RULES]
    assert "r0-auto" in ids and "r4-strict" in ids
    assert "shell-ask" in ids and "credential-deny" in ids
    assert all("priority" in r and "decision" in r for r in DEFAULT_RULES)
