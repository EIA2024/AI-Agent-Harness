"""Memory retrieval scoring helpers (dept 05 §5).

Hybrid ranking combines semantic similarity (embedding cosine), keyword
overlap, importance and recency into a single weighted score::

    score = 0.4 * cosine + 0.2 * keyword + 0.2 * importance + 0.2 * recency
"""

from __future__ import annotations

import re
from datetime import datetime

from ..model_gateway.embeddings import cosine_similarity, tokenize

_WORD_RE = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)


def keyword_score(query: str, content: str) -> float:
    """Fraction of query keywords present in the content (case-insensitive).

    ``query`` and ``content`` are lower-cased and split on whitespace and common
    separators; the score is ``|query_tokens ∩ content_tokens| / |query_tokens|``
    (0.0 for an empty query).
    """
    q_tokens = set(tokenize(query.lower()))
    c_tokens = set(tokenize(content.lower()))
    if not q_tokens:
        return 0.0
    return len(q_tokens & c_tokens) / len(q_tokens)


def recency_factor(created_at: datetime | None, half_life_days: float = 30.0) -> float:
    """Exponential time decay: ``0.5 ** (days / half_life_days)``.

    Dialect-safe: SQLite returns naive datetimes, PostgreSQL returns aware ones,
    so both are normalized to aware UTC before subtracting.
    """
    from ..common.utils import ensure_aware, utc_now

    if created_at is None:
        return 1.0
    created_at = ensure_aware(created_at)
    now = utc_now()
    days = (now - created_at).total_seconds() / 86_400.0
    if days < 0:
        return 1.0
    return 0.5 ** (days / half_life_days)


def _token_overlap(query: str, content: str) -> float:
    q = set(_WORD_RE.findall(query.lower()))
    c = set(_WORD_RE.findall(content.lower()))
    if not q:
        return 0.0
    return len(q & c) / len(q)


async def hybrid_rank(memory_rows: list, query: str, embeddings, top_k: int = 10) -> list[dict]:
    """Rank ``memory_rows`` (domain ``Memory`` objects) by hybrid score.

    Returns a list of dicts (sorted desc by score)::

        {"memory": Memory, "score": float, "components": {"cosine": ..., "keyword": ..., "importance": ..., "recency": ...}}
    """
    query_embedding = await embeddings.embed(query)
    ranked: list[dict] = []
    for mem in memory_rows:
        content_embedding = await embeddings.embed(mem.content)
        cosine = cosine_similarity(query_embedding, content_embedding)
        keyword = _token_overlap(query, mem.content) or keyword_score(query, mem.content)
        importance = float(getattr(mem, "importance", 0.5) or 0.5)
        recency = recency_factor(getattr(mem, "created_at", None))
        score = 0.4 * cosine + 0.2 * keyword + 0.2 * importance + 0.2 * recency
        ranked.append(
            {
                "memory": mem,
                "score": round(score, 6),
                "components": {
                    "cosine": round(cosine, 6),
                    "keyword": round(keyword, 6),
                    "importance": round(importance, 6),
                    "recency": round(recency, 6),
                },
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:top_k]
