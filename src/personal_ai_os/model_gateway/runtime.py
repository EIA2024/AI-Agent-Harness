"""Shared provider state, capabilities, and atomic runtime reloading."""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..common.models import ModelError, ModelRequest
from .config import (
    REASONING_EFFORTS,
    ProviderConfigStore,
    ProviderProfile,
    provider_identity,
    validate_provider_profile,
)
from .provider import AnthropicProvider, EchoProvider, OpenAICompatibleProvider
from .router import ModelRouter


class ProviderRuntimeError(RuntimeError):
    """Base error for public provider control operations."""


class ProviderReloadError(ProviderRuntimeError):
    """A candidate provider failed validation and was not installed."""


class ProviderBusyError(ProviderRuntimeError):
    """A provider change was rejected because a run is active."""


class ProviderConfigBoundaryError(ProviderRuntimeError):
    """The caller and API service do not share one local config directory."""


@dataclass(frozen=True)
class ProviderCapabilities:
    model_enumeration: bool
    reasoning_efforts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reasoning_efforts"] = list(self.reasoning_efforts)
        return data


@dataclass(frozen=True)
class ProviderStatus:
    mode: str
    profile: str | None
    format: str | None
    provider: str
    model: str
    reasoning_effort: str
    capabilities: ProviderCapabilities

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = self.capabilities.to_dict()
        return data


@dataclass(frozen=True)
class _ProviderBundle:
    routed: Any
    adapter: Any
    status: ProviderStatus


def provider_capabilities(
    profile: ProviderProfile | None,
    model: str,
) -> ProviderCapabilities:
    """Return only capabilities the active adapter explicitly supports."""
    if profile is None:
        return ProviderCapabilities(model_enumeration=False, reasoning_efforts=("auto",))
    model_enumeration = profile.format in ("openai", "anthropic")
    identity = provider_identity(profile)
    reasoning_model = model.lower().startswith(("o1", "o3", "o4", "gpt-5"))
    supports_effort = (
        profile.format == "openai"
        and identity == "openai"
        and reasoning_model
    )
    efforts = REASONING_EFFORTS if supports_effort else ("auto",)
    return ProviderCapabilities(
        model_enumeration=model_enumeration,
        reasoning_efforts=tuple(efforts),
    )


def _router_for(adapter: Any, model: str) -> ModelRouter:
    config = {
        "roles": {
            "router": [model],
            "worker": [model],
            "vision": [model],
            "embedding": [],
        }
    }
    return ModelRouter(providers={"primary": adapter}, config=config)


def build_provider_bundle(
    profile: ProviderProfile | None,
    *,
    require_key: bool = True,
) -> _ProviderBundle:
    """Build, but do not validate or install, a provider snapshot."""
    if profile is None:
        adapter = EchoProvider()
        status = ProviderStatus(
            mode="echo",
            profile=None,
            format=None,
            provider="echo",
            model="echo",
            reasoning_effort="auto",
            capabilities=provider_capabilities(None, "echo"),
        )
        return _ProviderBundle(routed=adapter, adapter=adapter, status=status)

    validate_provider_profile(profile)
    if require_key and not profile.api_key:
        raise ProviderReloadError(
            f"Provider profile {profile.name!r} has no API key in the OS keyring"
        )
    model = profile.model or (
        "claude-haiku-4-5" if profile.format == "anthropic" else "gpt-4o-mini"
    )
    capabilities = provider_capabilities(profile, model)
    effort = profile.reasoning_effort or "auto"
    if effort not in capabilities.reasoning_efforts:
        raise ProviderReloadError(
            f"Model {model!r} does not support reasoning effort {effort!r}"
        )
    if profile.format == "openai":
        adapter = OpenAICompatibleProvider(
            api_key=profile.api_key,
            base_url=profile.base_url or "https://api.openai.com/v1",
            default_model=model,
            reasoning_effort=effort,
            default_max_tokens=profile.max_tokens or 4096,
        )
    else:
        adapter = AnthropicProvider(
            api_key=profile.api_key,
            default_model=model,
        )
    status = ProviderStatus(
        mode="provider",
        profile=profile.name,
        format=profile.format,
        provider=provider_identity(profile),
        model=model,
        reasoning_effort=effort,
        capabilities=capabilities,
    )
    return _ProviderBundle(
        routed=_router_for(adapter, model),
        adapter=adapter,
        status=status,
    )


class ReloadableProvider:
    """Stable provider reference whose internal snapshot can be replaced."""

    provider_name = "reloadable"

    def __init__(self, provider: Any):
        self._provider = provider
        self._pinned: ContextVar[Any | None] = ContextVar(
            f"provider_snapshot_{id(self)}", default=None
        )

    @property
    def current(self) -> Any:
        return self._provider

    def replace(self, provider: Any) -> None:
        self._provider = provider

    def _snapshot(self) -> Any:
        return self._pinned.get() or self._provider

    @contextmanager
    def pin(self):
        """Keep one provider snapshot for all model calls in a run."""
        token = self._pinned.set(self._provider)
        try:
            yield
        finally:
            self._pinned.reset(token)

    async def complete(self, request: ModelRequest):
        return await self._snapshot().complete(request)

    async def stream(self, request: ModelRequest):
        provider = self._snapshot()
        stream = getattr(provider, "stream", None)
        if stream is not None:
            async for event in stream(request):
                yield event
            return
        response = await provider.complete(request)
        from ..common.models import ModelStreamEvent

        yield ModelStreamEvent(
            type="done",
            text=response.content or "",
            tool_call=response.tool_calls[0] if response.tool_calls else None,
            usage=response.usage,
        )

    async def health_check(self) -> bool:
        return await self._snapshot().health_check()


class ProviderRuntimeService:
    """Own the public provider state and perform validated atomic swaps."""

    def __init__(
        self,
        *,
        store: ProviderConfigStore | None = None,
        active_run_checker: Callable[[], Any] | None = None,
        bundle_factory: Callable[[ProviderProfile | None], _ProviderBundle] | None = None,
    ):
        self.store = store or ProviderConfigStore()
        self._active_run_checker = active_run_checker
        self._bundle_factory = bundle_factory or build_provider_bundle
        self._lock = asyncio.Lock()
        profile = self.store.get_active()
        if profile is None and os.environ.get("ANTHROPIC_API_KEY"):
            profile = ProviderProfile(
                name="anthropic-env",
                format="anthropic",
                api_key=os.environ["ANTHROPIC_API_KEY"],
                model="claude-haiku-4-5",
            )
        try:
            self._bundle = self._bundle_factory(profile)
        except ProviderReloadError:
            self._bundle = build_provider_bundle(None)
        self.provider = ReloadableProvider(self._bundle.routed)

    @property
    def status(self) -> ProviderStatus:
        return self._bundle.status

    def assert_shared_config_dir(self, config_dir: str) -> None:
        expected = self.store.path.parent.expanduser().resolve()
        supplied = Path(config_dir).expanduser().resolve()
        if supplied != expected:
            raise ProviderConfigBoundaryError(
                "Provider changes require the TUI and API service to share "
                "PERSONAL_AI_CONFIG_DIR"
            )

    async def _ensure_idle(self) -> None:
        if self._active_run_checker is None:
            return
        active = self._active_run_checker()
        if inspect.isawaitable(active):
            active = await active
        if active:
            raise ProviderBusyError(
                "Provider or model cannot be changed while a run is active"
            )

    async def reload(self) -> ProviderStatus:
        async with self._lock:
            await self._ensure_idle()
            return await self._reload_unlocked()

    async def _reload_unlocked(self) -> ProviderStatus:
        profile = self.store.get_active()
        candidate = self._bundle_factory(profile)
        if candidate.status.mode != "echo":
            try:
                healthy = await candidate.adapter.health_check()
            except Exception as exc:
                raise ProviderReloadError("Provider validation failed") from exc
            if not healthy:
                raise ProviderReloadError("Provider validation failed")
        self.provider.replace(candidate.routed)
        self._bundle = candidate
        return candidate.status

    async def list_models(self) -> list[str]:
        method = getattr(self._bundle.adapter, "list_models", None)
        if method is None:
            return []
        try:
            return await method()
        except ModelError:
            raise
        except Exception as exc:
            raise ModelError("Provider model enumeration failed") from exc

    async def update_model(
        self,
        *,
        model: str,
        reasoning_effort: str = "auto",
    ) -> ProviderStatus:
        async with self._lock:
            await self._ensure_idle()
            profile = self.store.get_active()
            if profile is None:
                raise ProviderReloadError("No active provider profile is configured")
            capabilities = provider_capabilities(profile, model)
            if reasoning_effort not in capabilities.reasoning_efforts:
                raise ProviderReloadError(
                    f"Model {model!r} does not support reasoning effort "
                    f"{reasoning_effort!r}"
                )
            old_model = profile.model
            old_effort = profile.reasoning_effort
            self.store.update(
                profile.name,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            try:
                return await self._reload_unlocked()
            except Exception:
                self.store.update(
                    profile.name,
                    model=old_model,
                    reasoning_effort=old_effort,
                )
                raise
