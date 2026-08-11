"""Conversation summarizer — MemGPT/Letta-style compaction (memory pressure).

When a session's conversation grows beyond the context budget, older turns are
compressed into a *running summary* instead of being dropped, so the gist of the
whole conversation survives within the model's context window (recent turns stay
in full; everything older lives as a compact summary). This mirrors the
"compaction" mechanism used by Letta/MemGPT and Claude Code.

Reference: https://docs.letta.com/v1-sdk/messages/compaction
"""

from __future__ import annotations

from ..common.models import ModelRequest

_SUMMARY_PROMPT = (
    "你是对话压缩器。把下面的历史对话压缩成一份简洁、按时间顺序的中文摘要，"
    "务必保留：\n"
    "- 用户身份信息与长期偏好（如姓名、职业、语言偏好、习惯）\n"
    "- 正在进行的任务、目标与已完成的动作和结果\n"
    "- 用户的明确要求、关键决定与待办事项\n"
    "- 重要的环境/项目背景\n"
    "用要点式，控制在 150 字以内的句子群。只依据给定对话，绝不编造。"
)


class ConversationSummarizer:
    """Compresses old conversation turns into a compact running summary."""

    def __init__(self, model_provider, *, max_summary_chars: int = 1500, max_tokens: int = 600):
        self.model_provider = model_provider
        self.max_summary_chars = max_summary_chars
        self.max_tokens = max_tokens

    async def summarize(
        self,
        messages: list[dict],
        existing_summary: str = "",
    ) -> str:
        """Merge ``messages`` into ``existing_summary`` and return the updated
        summary (empty input returns the existing summary unchanged)."""
        turns = "\n".join(
            f"{m.get('role')}: {m.get('content')}"
            for m in messages
            if isinstance(m, dict) and m.get("content")
        )
        if not turns.strip():
            return existing_summary

        parts = []
        if existing_summary.strip():
            parts.append(f"[已有摘要]\n{existing_summary}")
        parts.append(f"[新增对话]\n{turns}")
        prompt = _SUMMARY_PROMPT + "\n\n" + "\n\n".join(parts)

        request = ModelRequest(
            purpose="memory_extraction",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.max_tokens,
            temperature=0.2,
        )
        response = await self.model_provider.complete(request)
        summary = (response.content or "").strip()
        if not summary:
            summary = existing_summary  # fall back to previous on model failure
        return summary[: self.max_summary_chars]

    @staticmethod
    def wrap_for_prompt(summary: str) -> str:
        """Format the summary as an in-context memory block."""
        return f"[更早的对话摘要，帮助你保持对用户的连续记忆]\n{summary}"
