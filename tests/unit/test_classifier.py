"""TaskClassifier L0–L4 classification tests."""

from personal_ai_os.agent_runtime.classifier import TaskClassifier
from tests.unit.fakes import FakeModelProvider


async def test_l0_chat():
    result = await TaskClassifier().classify("你好，今天过得怎么样？")
    assert result["level"] == "L0"


async def test_l1_schedule_query():
    result = await TaskClassifier().classify("明天有什么安排？")
    assert result["level"] == "L1"
    assert result["reason"]


async def test_l2_multi_step():
    result = await TaskClassifier().classify("帮我整理这个项目并写学习指南")
    assert result["level"] == "L2"


async def test_l3_long_running():
    result = await TaskClassifier().classify("调查最近一个月的市场动态")
    assert result["level"] == "L3"


async def test_l4_recurring():
    result = await TaskClassifier().classify("每周五生成周报")
    assert result["level"] == "L4"


async def test_l4_daily_reminder():
    result = await TaskClassifier().classify("每天提醒我喝水")
    assert result["level"] == "L4"


async def test_all_levels_have_reason():
    for text in ("你好", "明天有什么安排", "帮我整理项目", "调查市场", "每周发周报"):
        result = await TaskClassifier().classify(text)
        assert result["level"] in {"L0", "L1", "L2", "L3", "L4"}
        assert isinstance(result["reason"], str) and result["reason"]


async def test_model_classification_used_when_provider_present():
    provider = FakeModelProvider([{"content": '{"level": "L3", "reason": "long research"}'}])
    result = await TaskClassifier(model_provider=provider).classify("任何输入")
    assert result["level"] == "L3"
    assert provider.i == 1


async def test_model_failure_falls_back_to_rules():
    class BrokenProvider(FakeModelProvider):
        async def complete(self, request):
            raise RuntimeError("model down")

    result = await TaskClassifier(model_provider=BrokenProvider([])).classify("帮我整理这个项目")
    assert result["level"] == "L2"
