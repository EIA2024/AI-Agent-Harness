"""Model Gateway — provider abstraction, routing and embeddings."""

from .config import KNOWN_PROVIDERS, ProviderConfigStore, ProviderProfile
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
    OpenAICompatibleProvider,
    estimate_cost_usd,
)
from .router import DEFAULT_CONFIG, ModelRouter

__all__ = [
    "AnthropicProvider",
    "EchoProvider",
    "FakeProvider",
    "OpenAICompatibleProvider",
    "ModelRouter",
    "DeterministicEmbedding",
    "EmbeddingProvider",
    "cosine_similarity",
    "tokenize",
    "estimate_cost_usd",
    "DEFAULT_CONFIG",
    "ProviderConfigStore",
    "ProviderProfile",
    "KNOWN_PROVIDERS",
]
