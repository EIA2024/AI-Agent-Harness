"""Model Gateway — provider abstraction, routing and embeddings."""

from .embeddings import (
    DeterministicEmbedding,
    EmbeddingProvider,
    cosine_similarity,
    tokenize,
)
from .provider import (
    AnthropicProvider,
    EchoProvider,
    FakeProvider,
    estimate_cost_usd,
)
from .router import DEFAULT_CONFIG, ModelRouter

__all__ = [
    "AnthropicProvider",
    "EchoProvider",
    "FakeProvider",
    "ModelRouter",
    "DeterministicEmbedding",
    "EmbeddingProvider",
    "cosine_similarity",
    "tokenize",
    "estimate_cost_usd",
    "DEFAULT_CONFIG",
]
