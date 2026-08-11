"""Context Engine — assembles the prompt for a single Agent run (blueprint §13).

Builds the 4-tier context from ``AgentState``:

- **Tier 1** — stable system identity + security policy + tool protocol (from
  ``prompts/*.md`` files, cache-friendly).
- **Tier 2** — user profile (memory scope=``profile``) + skill/tool index.
- **Tier 3** — relevant memories + task/plan state + recent conversation.
- **Tier 4** — current user input + this round's tool results.

Trust labels are honored: any *untrusted* content is wrapped with
``utils.untrusted_wrapper`` and placed in a ``user`` message (never in the
system message), so the model sees it as data, not instructions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from personal_ai_os.common.models import MemoryQuery, TrustLevel
from personal_ai_os.common.utils import approximate_tokens, untrusted_wrapper
from personal_ai_os.context_engine.budget import ContextBudget

_UNTRUSTED = {
    TrustLevel.UNTRUSTED_WEB.value,
    TrustLevel.UNTRUSTED_EMAIL.value,
    TrustLevel.UNTRUSTED_DOCUMENT.value,
    TrustLevel.UNTRUSTED_MCP.value,
}

_DEFAULT_SECTIONS = ("system_identity", "user_profile", "skills", "memories",
                     "task_state", "conversation_summary", "conversation", "tool_results")


class ContextEngine:
    """Injected into the Agent Runtime's ``build_context`` node."""

    def __init__(
        self,
        *,
        memory_store: Any | None = None,
        tool_registry: Any | None = None,
        budget: ContextBudget | None = None,
        prompt_dir: str | Path | None = None,
    ) -> None:
        self.memory_store = memory_store
        self.tool_registry = tool_registry
        self.budget = budget or ContextBudget()
        self.prompt_dir = Path(prompt_dir) if prompt_dir else Path(__file__).parent / "prompts"

    # ------------------------------------------------------------------ build

    async def build(self, state: dict, *, available_tokens: int | None = None) -> dict:
        """Assemble the 4-tier context for ``state``.

        Returns::

            {
                "messages": [...],          # ready to send to the model
                "system_prompt": str,
                "token_count": int,
                "sections": {section: text},
                "context_items": [...],     # per-section ContextItem dicts
                "budget_usage": {...},
            }
        """
        budget = self._budget_for(available_tokens)
        budget.reset()

        # ---- Tier 1: stable identity + security + tool protocol ------------
        tier1 = self._tier1()
        tier1 = budget.fit("system_identity", tier1)
        budget.tally("system_identity", tier1)

        # ---- Tier 2: user profile + skill/tool index -----------------------
        profile = await self._tier2_profile(state)
        profile = budget.fit("user_profile", profile)
        budget.tally("user_profile", profile)

        skills = await self._tier2_skills(state)
        skills = budget.fit("skills", skills)
        budget.tally("skills", skills)

        # ---- Tier 3: memories + task state + conversation ------------------
        memories = await self._tier3_memories(state)
        memories = budget.fit("memories", memories)
        budget.tally("memories", memories)

        task_state = self._tier3_task_state(state)
        task_state = budget.fit("task_state", task_state)
        budget.tally("task_state", task_state)

        conversation = self._tier3_conversation(state, budget)

        # ---- Tier 4: current user input + tool results ---------------------
        tool_messages = self._tier4_tool_results(state, budget)

        # ---- Assemble -------------------------------------------------------
        system_prompt = "\n\n".join(
            p for p in (tier1, profile, skills, task_state, memories) if p
        )

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(conversation)
        messages.extend(self._wrap_untrusted_context(state))
        messages.extend(tool_messages)
        messages.append({"role": "user", "content": state.get("user_input", "")})

        token_count = sum(approximate_tokens(m.get("content") or "") for m in messages)

        reserve_ratio = self.budget.allocations.get("generation_reserve", 0.10)
        reserve_tokens = (
            int(available_tokens * reserve_ratio) if available_tokens else budget.token_limit("generation_reserve")
        )

        sections = {
            "system_identity": tier1,
            "user_profile": profile,
            "skills": skills,
            "memories": memories,
            "task_state": task_state,
            "conversation_summary": state.get("conversation_summary") or "",
            "conversation": "\n".join((m.get("content") or "") for m in conversation),
            "tool_results": "\n".join((m.get("content") or "") for m in tool_messages),
            "user_input": state.get("user_input", ""),
            "generation_reserve": str(reserve_tokens),
        }

        context_items = [
            self._context_item(section, sections[section])
            for section in _DEFAULT_SECTIONS
            if sections.get(section)
        ]

        return {
            "messages": messages,
            "system_prompt": system_prompt,
            "token_count": token_count,
            "sections": sections,
            "context_items": context_items,
            "budget_usage": budget.used(),
        }

    # ---------------------------------------------------------------- tiers

    def _budget_for(self, available_tokens: int | None) -> ContextBudget:
        if not available_tokens:
            return self.budget
        reserve = self.budget.allocations.get("generation_reserve", 0.10)
        allocs = {k: v for k, v in self.budget.allocations.items() if k != "generation_reserve"}
        total = sum(allocs.values()) or 1.0
        allocs = {k: v / total for k, v in allocs.items()}
        return ContextBudget(
            max_tokens=max(1, int(available_tokens * (1.0 - reserve))),
            allocations=allocs,
        )

    def _tier1(self) -> str:
        parts = [
            self._load_prompt("identity.md"),
            self._load_prompt("security.md"),
            self._load_prompt("tool_policy.md"),
        ]
        return "\n\n".join(p for p in parts if p)

    async def _tier2_profile(self, state: dict) -> str:
        if self.memory_store is None or not state.get("owner_id"):
            return ""
        query = MemoryQuery(
            owner_id=UUID(str(state["owner_id"])),
            query="user profile",
            scope="profile",
            types=["profile"],
            limit=5,
        )
        try:
            memories = await self.memory_store.search(query)
        except Exception:  # memory engine failure must not break context build
            return ""
        if not memories:
            return ""
        lines = [f"- {m.content}" for m in memories]
        return "[用户资料]\n" + "\n".join(lines)

    async def _tier2_skills(self, state: dict) -> str:
        if self.tool_registry is None:
            return ""
        try:
            tools = self.tool_registry.list_tools()
            if hasattr(tools, "__await__"):
                tools = await tools
        except Exception:
            return ""
        if not tools:
            return ""
        lines = [f"- {t.name}: {t.description}" for t in tools]
        return "[可用工具]\n" + "\n".join(lines)

    async def _tier3_memories(self, state: dict) -> str:
        if self.memory_store is None or not state.get("owner_id"):
            return ""
        query = MemoryQuery(
            owner_id=UUID(str(state["owner_id"])),
            query=state.get("user_input", ""),
            scope=None,
            limit=10,
        )
        try:
            memories = await self.memory_store.search(query)
        except Exception:
            return ""
        if not memories:
            return ""
        lines = []
        for m in memories:
            score = f"(score={m.score:.2f}) " if m.score is not None else ""
            lines.append(f"- {score}{m.content}")
        return "[相关记忆]\n" + "\n".join(lines)

    def _tier3_task_state(self, state: dict) -> str:
        parts: list[str] = []
        task = state.get("task")
        if isinstance(task, dict):
            level = task.get("level", "L0")
            reason = task.get("reason") or task.get("classification_reason") or ""
            parts.append(f"任务级别: {level}" + (f"（{reason}）" if reason else ""))
        plan = state.get("plan") or []
        if plan:
            lines = []
            for i, step in enumerate(plan):
                status = step.get("status", "pending")
                objective = step.get("objective", "")
                lines.append(f"{i + 1}. [{status}] {objective}")
            parts.append("执行计划:\n" + "\n".join(lines))
        current_step = state.get("current_step", 0)
        if plan:
            parts.append(f"当前进度: 第 {current_step + 1} / {len(plan)} 步")
        return "\n".join(parts)

    def _tier3_conversation(self, state: dict, budget: ContextBudget) -> list[dict]:
        # MemGPT-style compaction: older turns live as a compact summary (never
        # dropped), the most-recent turns stay verbatim. The summary is a
        # system-frame so the model reads it as memory, not something to reply to.
        items: list[dict] = []
        summary = state.get("conversation_summary") or ""
        if summary and summary.strip():
            summary_budget = budget.token_limit("conversation_summary")
            if budget.tokens_of(summary) > summary_budget:
                summary = summary[: max(0, summary_budget * 4 - 40)] + " ..."
            items.append(
                {
                    "role": "system",
                    "content": f"[更早的对话摘要，帮助你保持对用户的连续记忆]\n{summary}",
                }
            )

        history = state.get("messages") or []
        # Preserve the natural interleaved order from the runtime: an assistant
        # tool-call frame must stay adjacent to its tool result, otherwise
        # OpenAI-compatible endpoints (DeepSeek in particular) reject the turn.
        kept = [
            m for m in history
            if isinstance(m, dict) and m.get("role") in ("user", "assistant", "system", "tool")
        ]
        budget_total = budget.token_limit("conversation") + budget.token_limit("tool_results")
        items.extend(self._fit_tail(kept[-10:], budget_total))
        return items

    def _tier4_tool_results(self, state: dict, budget: ContextBudget) -> list[dict]:
        # In the real flow the runtime (observe) already folds tool frames into
        # state.messages right after their assistant tool-call frame, and the
        # conversation tier passes them through in that adjacency-preserving
        # order. Re-adding them here would break the required adjacency. Only
        # when messages carry no tool frames (standalone / first turn) do we
        # synthesize them from tool_results.
        if any(isinstance(m, dict) and m.get("role") == "tool" for m in (state.get("messages") or [])):
            return []

        results = state.get("tool_results") or []
        messages: list[dict] = []
        for tr in results[-5:]:
            content = self._tool_result_text(tr)
            trust = tr.get("trust") if isinstance(tr, dict) else getattr(tr, "trust", None)
            if trust in _UNTRUSTED:
                prefix, suffix = untrusted_wrapper(tr.get("source") or "tool")
                content = prefix + content + suffix
            messages.append(
                {
                    "role": "tool",
                    "content": content,
                    "tool_call_id": tr.get("tool_call_id"),
                    "name": tr.get("tool_name"),
                }
            )
        return self._fit_tail(messages, budget.token_limit("tool_results"))

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _tool_result_text(tr: Any) -> str:
        if isinstance(tr, dict):
            text = tr.get("text")
            if text:
                return text
            data = tr.get("data")
            if data:
                return json.dumps(data, ensure_ascii=False)
            return str(tr.get("error") or tr.get("content") or "")
        return str(getattr(tr, "text", None) or getattr(tr, "content", "") or "")

    @staticmethod
    def _fit_tail(items: list[dict], limit: int) -> list[dict]:
        """Keep the newest items whose combined tokens fit under ``limit``."""
        if limit <= 0:
            return []
        kept: list[dict] = []
        total = 0
        for item in reversed(items):
            n = approximate_tokens(item.get("content") or "")
            if total + n > limit:
                if not kept:  # even one item doesn't fit → truncate the newest
                    keep_chars = max(0, limit * 4 - len(" ... [截断]"))
                    truncated = dict(item)
                    truncated["content"] = (item.get("content") or "")[:keep_chars] + " ... [截断]"
                    kept.append(truncated)
                break
            kept.append(item)
            total += n
        kept.reverse()
        return kept

    def _wrap_untrusted_context(self, state: dict) -> list[dict]:
        """Render untrusted context items as DATA-wrapped ``user`` messages."""
        items = state.get("context_items") or []
        messages: list[dict] = []
        for item in items:
            content = self._field(item, "content")
            trust = str(self._field(item, "trust") or "")
            if trust not in _UNTRUSTED:
                continue
            source = str(self._field(item, "source") or "external")
            prefix, suffix = untrusted_wrapper(source)
            messages.append(
                {
                    "role": "user",
                    "content": prefix + content + suffix,
                    "metadata": {"trust": trust, "source": source, "wrapped": True},
                }
            )
        return messages

    def _load_prompt(self, name: str) -> str:
        path = self.prompt_dir / name
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _context_item(self, section: str, content: str) -> dict:
        return {
            "content": content,
            "source": "system",
            "trust": TrustLevel.TRUSTED_SYSTEM.value,
            "type": section,
            "metadata": {"tokens": approximate_tokens(content)},
        }

    @staticmethod
    def _field(item: Any, name: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(name, default)
        return getattr(item, name, default)
