"""Context budget management (blueprint §13.2).

The model's context window is carved up into sections by percentage. Each
section has a hard token ceiling; content that exceeds its ceiling is truncated
by :meth:`ContextBudget.fit`. Token counting is intentionally cheap — the shared
:func:`personal_ai_os.common.utils.approximate_tokens` (chars / 4) — since the
runtime only needs a stable, fast ordering, not an exact tokenizer.
"""

from __future__ import annotations

from collections import defaultdict

from personal_ai_os.common.utils import approximate_tokens

# Default allocations from blueprint §13.2. Sums to 1.0.
DEFAULT_ALLOCATIONS: dict[str, float] = {
    "system_identity": 0.10,
    "user_profile": 0.05,
    "skills": 0.08,
    "memories": 0.12,
    "conversation": 0.25,
    "task_state": 0.15,
    "tool_results": 0.15,
    "generation_reserve": 0.10,
}

# Marker appended when content is truncated to its section budget.
_TRUNC_MARKER = " ... [截断]"


class ContextBudget:
    """Per-section token ceilings derived from a percentage allocation."""

    def __init__(self, *, max_tokens: int = 16000, allocations: dict | None = None) -> None:
        self.max_tokens = max(1, int(max_tokens))
        self.allocations = dict(DEFAULT_ALLOCATIONS if allocations is None else allocations)
        self._used: dict[str, int] = defaultdict(int)

    # -- queries ------------------------------------------------------------

    def token_limit(self, section: str) -> int:
        """Max tokens a ``section`` may consume within this budget."""
        return int(self.max_tokens * self.allocations.get(section, 0.0))

    def tokens_of(self, text: str) -> int:
        return approximate_tokens(text)

    # -- mutators -----------------------------------------------------------

    def fit(self, section: str, text: str) -> str:
        """Return ``text`` truncated to fit the section's token ceiling.

        Truncation is applied on the underlying character budget
        (``limit * 4`` chars ≈ limit tokens) so the result is predictable.
        """
        if text is None:
            return ""
        text = str(text)
        limit = self.token_limit(section)
        if limit <= 0:
            return ""
        if approximate_tokens(text) <= limit:
            return text
        keep_chars = max(0, limit * 4 - len(_TRUNC_MARKER))
        return text[:keep_chars] + _TRUNC_MARKER

    def tally(self, section: str, text: str) -> None:
        """Accumulate ``text``'s token estimate against the section's usage."""
        self._used[section] += approximate_tokens(text or "")

    # -- introspection ------------------------------------------------------

    def used(self) -> dict[str, int]:
        """Current per-section usage map (copy)."""
        return dict(self._used)

    def reset(self) -> None:
        self._used.clear()

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<ContextBudget max={self.max_tokens}>"
