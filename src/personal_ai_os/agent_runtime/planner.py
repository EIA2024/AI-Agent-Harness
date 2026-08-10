"""Plan generation for multi-step (L2) and long-running (L3) tasks (§8).

MVP is rule-based: a 2–4 step plan is derived from the user request's intent
verbs. A ``model_provider`` can be injected to generate richer plans; the rules
are the fallback on any model error.
"""

from __future__ import annotations

import json
import re
from typing import Any

from personal_ai_os.common.models import ModelRequest

_PLAN_MODEL_PROMPT = (
    "为一个多步骤任务生成执行计划。\n"
    "只输出 JSON 数组，每项含 objective / success_criteria / status(\"pending\")。\n"
    "任务: {input}"
)


class Planner:
    """Produces ``[{"id", "objective", "success_criteria", "status"}, ...]``."""

    def __init__(self, model_provider: Any | None = None) -> None:
        self.model_provider = model_provider

    async def create_plan(self, user_input: str, state: dict | None = None) -> list[dict]:
        """Return a plan of 2–4 steps for ``user_input``."""
        if self.model_provider is not None and hasattr(self.model_provider, "complete"):
            plan = await self._model_plan(user_input)
            if plan:
                return plan
        return self._rule_based(user_input)

    # ---------------------------------------------------------------- rules

    def _rule_based(self, user_input: str) -> list[dict]:
        lowered = user_input.lower()

        if any(kw in lowered for kw in ("调查", "研究", "investigate", "research", "跟踪")):
            return self._steps(
                ("收集并调查相关背景资料", "掌握主题的事实、关键参与方与时间线"),
                ("整理与分析关键发现", "形成结构化结论，标注来源与不确定项"),
                ("撰写调研报告", "产出可读的书面报告供用户使用"),
            )
        if any(kw in lowered for kw in ("整理", "organize", "梳理")):
            return self._steps(
                ("收集与该项目相关的全部资料", "列出完整资料清单"),
                ("按主题整理结构与要点", "产出分类清晰的结构化笔记"),
                ("编写学习指南/总结", "产出最终可交付的文档"),
            )
        if any(kw in lowered for kw in ("分析", "analyze", "总结", "summarize", "评估")):
            return self._steps(
                ("收集需要分析的数据或素材", "素材齐全并可引用"),
                ("完成核心分析", "得出有依据的结论"),
                ("总结并呈现结论", "以用户易懂的形式输出"),
            )
        return self._steps(
            ("明确任务目标与所需输入", "目标清晰、输入就绪"),
            ("执行核心工作", "完成主体内容"),
            ("检查并交付结果", "结果准确且已交付用户"),
        )

    @staticmethod
    def _steps(*pairs: tuple[str, str]) -> list[dict]:
        steps: list[dict] = []
        for i, (objective, success) in enumerate(pairs, start=1):
            steps.append(
                {
                    "id": f"step-{i}",
                    "objective": objective,
                    "success_criteria": success,
                    "status": "pending",
                }
            )
        return steps

    # ---------------------------------------------------------------- model

    async def _model_plan(self, user_input: str) -> list[dict] | None:
        try:
            response = await self.model_provider.complete(
                ModelRequest(
                    purpose="plan",
                    messages=[
                        {"role": "system", "content": "你是任务规划器，只输出 JSON 数组。"},
                        {"role": "user", "content": _PLAN_MODEL_PROMPT.format(input=user_input)},
                    ],
                )
            )
            text = (response.content or "").strip()
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if not match:
                return None
            raw = json.loads(match.group(0))
            if not isinstance(raw, list) or not raw:
                return None
            steps: list[dict] = []
            for i, item in enumerate(raw[:4], start=1):
                steps.append(
                    {
                        "id": f"step-{i}",
                        "objective": str(item.get("objective", "")),
                        "success_criteria": str(item.get("success_criteria", "")),
                        "status": "pending",
                    }
                )
            return steps or None
        except Exception:
            return None
