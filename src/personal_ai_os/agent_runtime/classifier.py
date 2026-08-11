"""Task classification (L0–L4) for the Agent Runtime (blueprint §3, §8.1).

Levels:

- **L0 Chat** — casual chat / knowledge Q&A → direct answer.
- **L1 One-shot tool** — a single lookup ("tomorrow's schedule") → tool, then answer.
- **L2 Multi-step** — "organize this project and write a guide" → explicit plan.
- **L3 Long-running** — "investigate the market over a month" → plan + resumable.
- **L4 Proactive/recurring** — "weekly report every Friday" → hand off to Scheduler.

MVP is rule-based (no model required). If a ``model_provider`` is injected the
classifier *tries* a model classification first and falls back to the rules on
any error.
"""

from __future__ import annotations

import json
import re
from typing import Any

from personal_ai_os.common.models import ModelRequest

_LEVELS = ("L0", "L1", "L2", "L3", "L4")

# Priority order is significant: first match wins. L4/L3 (more specific by
# nature) are evaluated before L1/L2 so recurring and long-running keywords
# take precedence over single-step keywords.
_RULES: list[tuple[str, tuple[str, ...], str]] = [
    # (level, keywords, reason-template)
    ("L4", ("每周", "每天", "每月", "定时", "提醒我", "每天早晨", "every week",
            "every day", "daily", "weekly", "monthly", "remind me", "recurring"), "周期/主动任务"),
    ("L3", ("调查", "长时间", "持续追踪", "investigate", "long-running", "long term",
            "track over"), "长时间运行任务"),
    ("L1", ("几点", "日程", "时间安排", "安排", "天气", "schedule", "calendar",
            "what time", "when is", "tomorrow"), "单次查询任务"),
    ("L2", ("帮我", "整理", "分析", "研究", "总结", "规划", "编写", "organize",
            "analyze", "summarize", "plan out", "put together", "compile"), "多步骤任务"),
]

_MODEL_CLASSIFY_PROMPT = (
    "把下面的用户请求分类为 L0(闲聊/问答) / L1(单次工具查询) / "
    "L2(多步骤任务) / L3(长时间运行) / L4(周期性自动化) 之一。\n"
    "只输出 JSON，格式: {{\"level\": \"L0\", \"reason\": \"一句话理由\"}}\n"
    "用户请求: {input}"
)


class TaskClassifier:
    """Classifies a user input into an L0–L4 task level."""

    def __init__(self, model_provider: Any | None = None) -> None:
        self.model_provider = model_provider

    async def classify(self, user_input: str) -> dict:
        """Return ``{"level": ..., "reason": ...}``."""
        if self.model_provider is not None and hasattr(self.model_provider, "complete"):
            level = await self._model_classify(user_input)
            if level is not None:
                return level
        return self._rule_based(user_input)

    # ---------------------------------------------------------------- rules

    def _rule_based(self, user_input: str) -> dict:
        lowered = user_input.lower()
        for level, keywords, reason in _RULES:
            for kw in keywords:
                if kw.lower() in lowered:
                    return {"level": level, "reason": f"{reason}（命中关键词“{kw}”）"}
        return {"level": "L0", "reason": "闲聊或普通问答，无工具/规划需求"}

    # ---------------------------------------------------------------- model

    async def _model_classify(self, user_input: str) -> dict | None:
        try:
            response = await self.model_provider.complete(
                ModelRequest(
                    purpose="classify",
                    messages=[
                        {"role": "system", "content": "你是任务分类器，只输出 JSON。"},
                        {"role": "user", "content": _MODEL_CLASSIFY_PROMPT.format(input=user_input)},
                    ],
                )
            )
            text = (response.content or "").strip()
            if not text:
                return None
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return None
            payload = json.loads(match.group(0))
            level = str(payload.get("level", "")).upper()
            if level not in _LEVELS:
                return None
            return {"level": level, "reason": str(payload.get("reason") or "模型分类")}
        except Exception:
            return None
