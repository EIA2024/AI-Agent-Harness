"""Planner tests — L2/L3 tasks produce 2–4 step plans."""

from personal_ai_os.agent_runtime.planner import Planner
from tests.unit.fakes import FakeModelProvider


async def test_l2_plan_structure():
    plan = await Planner().create_plan("帮我整理这个项目并写学习指南", {})
    assert 2 <= len(plan) <= 4
    for step in plan:
        assert step["id"].startswith("step-")
        assert step["objective"]
        assert step["success_criteria"]
        assert step["status"] == "pending"


async def test_research_plan_mentions_research():
    plan = await Planner().create_plan("调查最近一个月的市场动态", {})
    objectives = " ".join(s["objective"] for s in plan)
    assert any(kw in objectives for kw in ("调查", "调研", "研究"))


async def test_empty_input_still_returns_plan():
    plan = await Planner().create_plan("", {})
    assert 2 <= len(plan) <= 4


async def test_model_plan_when_provider_present():
    provider = FakeModelProvider(
        [
            {
                "content": (
                    '[{"objective": "第一步", "success_criteria": "完成", "status": "pending"},'
                    '{"objective": "第二步", "success_criteria": "交付", "status": "pending"}]'
                )
            }
        ]
    )
    plan = await Planner(model_provider=provider).create_plan("帮我做个研究", {})
    assert len(plan) == 2
    assert plan[0]["objective"] == "第一步"
    assert plan[1]["id"] == "step-2"


async def test_model_failure_falls_back_to_rules():
    class BrokenProvider(FakeModelProvider):
        async def complete(self, request):
            raise RuntimeError("model down")

    plan = await Planner(model_provider=BrokenProvider([])).create_plan("帮我整理项目", {})
    assert 2 <= len(plan) <= 4
