"""Deterministic, dependency-free text embeddings.

``DeterministicEmbedding`` hashes every (position, token) pair into a fixed
dimension so identical texts produce identical vectors and semantically
overlapping texts produce correlated vectors. No external API required — used
for local mode and tests. A future ``EmbeddingProvider`` can swap in a real
embedding model (e.g. OpenAI ``text-embedding-3-small``).
"""

from __future__ import annotations

import hashlib
import math
import re

_DIM = 384

#: Common separators: whitespace + punctuation. CJK characters are split into
#: individual characters so Chinese text is tokenised meaningfully.
_SEP_RE = re.compile(r"[\s,.;:!?()\"'`~@#$%^&*_\-+=|/\\<>\[\]{}、。，；：！？（）「」『』【】《》…·]+")
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")


def tokenize(text: str) -> list[str]:
    """Split text into tokens: latin/digit words + individual CJK characters."""
    tokens: list[str] = []
    for part in _SEP_RE.split(text or ""):
        if not part:
            continue
        i = 0
        while i < len(part):
            if _CJK_RE.match(part[i]):
                tokens.append(part[i])
                i += 1
            else:
                j = i
                while j < len(part) and not _CJK_RE.match(part[j]):
                    j += 1
                tokens.append(part[i:j])
                i = j
    return tokens


class DeterministicEmbedding:
    """Hash-based embedding (dim=384) with position-weighted token hashing."""

    def __init__(self, *, dim: int = _DIM):
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for pos, tok in enumerate(tokenize(text)):
            digest = hashlib.blake2b(f"{pos}:{tok}".encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            weight = (int.from_bytes(digest[4:8], "big") / 2**32) * 2.0 - 1.0
            vec[idx] += weight
        return _normalize(vec)

    async def embed(self, text: str) -> list[float]:
        return self._vector(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class EmbeddingProvider:
    """Optional facade over a real embedding model.

    MVP defaults to :class:`DeterministicEmbedding`; a production build can
    inject a remote embedding adapter with the same ``embed`` / ``embed_many``
    interface.
    """

    def __init__(self, *, backend: DeterministicEmbedding | None = None):
        self.backend = backend or DeterministicEmbedding()

    async def embed(self, text: str) -> list[float]:
        return await self.backend.embed(text)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return await self.backend.embed_many(texts)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (0.0 if either is zero)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    if dot == 0.0:
        return 0.0
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
