"""Tests for deterministic embeddings and cosine similarity."""

from __future__ import annotations

import math

from personal_ai_os.model_gateway.embeddings import (
    DeterministicEmbedding,
    EmbeddingProvider,
    cosine_similarity,
    tokenize,
)


async def test_embedding_is_deterministic():
    emb = DeterministicEmbedding()
    v1 = await emb.embed("用户喜欢Python")
    v2 = await emb.embed("用户喜欢Python")
    assert v1 == v2
    assert len(v1) == 384


async def test_embedding_is_normalized():
    emb = DeterministicEmbedding()
    vec = await emb.embed("hello world 你好")
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-6


async def test_cosine_identity_and_zero():
    emb = DeterministicEmbedding()
    vec = await emb.embed("memory system")
    assert abs(cosine_similarity(vec, vec) - 1.0) < 1e-9
    assert cosine_similarity([], []) == 0.0
    zero = [0.0] * 384
    assert cosine_similarity(vec, zero) == 0.0


async def test_similar_texts_are_closer():
    emb = DeterministicEmbedding()
    base = await emb.embed("我喜欢Python")
    similar = await emb.embed("我喜欢Python编程")
    different = await emb.embed("我要去纽约旅游")

    sim = cosine_similarity(base, similar)
    diff = cosine_similarity(base, different)
    assert sim > 0.5
    assert sim > diff + 0.1


async def test_embed_many_returns_vectors():
    emb = DeterministicEmbedding()
    vectors = await emb.embed_many(["a", "b", "c"])
    assert len(vectors) == 3
    assert all(len(v) == 384 for v in vectors)


async def test_embedding_provider_delegates():
    provider = EmbeddingProvider()
    v1 = await provider.embed("hello")
    v2 = await provider.embed("hello")
    assert v1 == v2


def test_tokenize_splits_cjk_and_latin():
    tokens = tokenize("我喜欢Python，以及JavaScript.")
    assert tokens == ["我", "喜", "欢", "Python", "以", "及", "JavaScript"]
