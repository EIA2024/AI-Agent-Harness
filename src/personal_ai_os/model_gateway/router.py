"""Model router — picks a (provider, model) for each request and fails over.

Routing strategy (dept 10 §5)::

    intent_classification / memory_extraction  -> router  role (cheap/fast)
    assistant                                  -> worker  role (balanced)
    reasoning / planning                       -> worker  role + high quality
    vision                                     -> vision  role
    embedding                                  -> embedding role
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..common.models import ModelError, ModelRequest, ModelResponse

if TYPE_CHECKING:  # pragma: no cover
    from ..common.protocols import ModelProvider

DEFAULT_CONFIG: dict = {
    "roles": {
        "router": ["claude-haiku-4-5"],
        "worker": ["claude-sonnet-5"],
        "vision": ["claude-sonnet-5"],
        "embedding": [],
    },
}

#: purpose -> (role, high_quality)
_PURPOSE_ROLES: dict[str, tuple[str, bool]] = {
    "intent_classification": ("router", False),
    "memory_extraction": ("router", False),
    "assistant": ("worker", False),
    "reasoning": ("worker", True),
    "planning": ("worker", True),
    "vision": ("vision", False),
    "embedding": ("embedding", False),
}


class ModelRouter:
    """Routes ``ModelRequest`` to a concrete provider + model id."""

    def __init__(self, *, providers: dict[str, ModelProvider] | None = None, config: dict | None = None):
        self.providers: dict[str, ModelProvider] = {}
        self._order: list[str] = []
        self.config = config or DEFAULT_CONFIG
        for name, provider in (providers or {}).items():
            self.add_provider(provider, name=name)

    # -- registration ------------------------------------------------------

    def add_provider(self, provider: ModelProvider, *, name: str | None = None) -> None:
        """Register a provider. Uses ``provider.provider_name`` unless overridden."""
        key = name or getattr(provider, "provider_name", "provider")
        self.providers[key] = provider
        if key not in self._order:
            self._order.append(key)

    # -- routing -----------------------------------------------------------

    @staticmethod
    def role_for_purpose(purpose: str) -> str:
        """Map a request purpose to a model role (see dept 10 §5)."""
        role, _ = _PURPOSE_ROLES.get(purpose, ("worker", False))
        return role

    def _models_for_role(self, role: str, *, high_quality: bool = False) -> list[str]:
        roles = self.config.get("roles", {})
        if high_quality:
            high_models = roles.get(f"{role}_high") or []
            if high_models:
                return list(high_models)
        return list(roles.get(role, []))

    def _provider_serves(self, provider: ModelProvider, model: str) -> bool:
        known = getattr(provider, "models", None)
        if not known:  # wildcard provider
            return True
        return model in known

    def select(self, request: ModelRequest) -> tuple[ModelProvider, str]:
        """Pick the best (provider, model) for ``request``.

        Selection order:

        1. ``preferred_provider`` (if registered)
        2. ``preferred_model`` (first registered provider serving it)
        3. role-based model from ``config``, first provider serving it
        4. first registered provider, ``preferred_model`` or a configured model
        """
        providers = self._ordered_providers()
        if not providers:
            raise ModelError("No model providers registered with the router.")

        # 1. explicit provider preference
        if request.preferred_provider and request.preferred_provider in self.providers:
            provider = self.providers[request.preferred_provider]
            return provider, request.preferred_model or self._default_model_for(provider, request)

        # 2. explicit model preference
        if request.preferred_model:
            for provider in providers:
                if self._provider_serves(provider, request.preferred_model):
                    return provider, request.preferred_model

        # 3. role-based routing
        role = self.role_for_purpose(request.purpose)
        _, high_quality = _PURPOSE_ROLES.get(request.purpose, ("worker", False))
        if request.quality_class in ("high", "max"):
            high_quality = True
        for model in self._models_for_role(role, high_quality=high_quality):
            for provider in providers:
                if self._provider_serves(provider, model):
                    return provider, model

        # 4. fall back to the first provider
        provider = providers[0]
        return provider, request.preferred_model or self._default_model_for(provider, request)

    def _default_model_for(self, provider: ModelProvider, request: ModelRequest) -> str:
        default = getattr(provider, "default_model", None)
        if default:
            return default
        models = getattr(provider, "models", None)
        if models:
            return models[0]
        return "default"

    def _ordered_providers(self) -> list[ModelProvider]:
        return [self.providers[name] for name in self._order]

    # -- completion --------------------------------------------------------

    def _candidates(self, request: ModelRequest) -> list[tuple[ModelProvider, str]]:
        """Ordered list of (provider, model) candidates for failover."""
        first = self.select(request)
        candidates = [first]
        for provider in self._ordered_providers():
            if provider is first[0]:
                continue
            model = request.preferred_model
            if model is None:
                # try to serve the same model picked by select()
                model = first[1] if self._provider_serves(provider, first[1]) else None
                if model is None:
                    model = getattr(provider, "default_model", None) or first[1]
            candidates.append((provider, model))
        return candidates

    @staticmethod
    def _with_model(request: ModelRequest, provider: ModelProvider, model: str) -> ModelRequest:
        """Return a routed copy of ``request`` bound to the chosen provider/model."""
        return ModelRequest(
            purpose=request.purpose,
            messages=request.messages,
            tools=request.tools,
            latency_class=request.latency_class,
            quality_class=request.quality_class,
            max_cost=request.max_cost,
            preferred_provider=provider.provider_name,
            preferred_model=model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            response_format=request.response_format,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Route -> call, failing over to the next provider once on ``ModelError``.

        Per dept 10 §7 we never retry more than two providers.
        """
        candidates = self._candidates(request)
        attempts = min(len(candidates), 2)
        last_error: ModelError | None = None
        for provider, model in candidates[:attempts]:
            try:
                routed = self._with_model(request, provider, model)
                return await provider.complete(routed)
            except ModelError as exc:
                last_error = exc
                continue
        raise ModelError(f"All providers failed for purpose={request.purpose!r}: {last_error}")
