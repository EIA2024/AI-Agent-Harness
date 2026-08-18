"""Provider model selection logic shared by the TUI integration layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from personal_ai_os.cli.api.dto import (
    ProviderModelsDTO,
    ProviderStatusDTO,
)
from personal_ai_os.model_gateway import ProviderConfigStore

REASONING_EFFORTS = ("auto", "low", "medium", "high")


class ModelAPI(Protocol):
    async def get_provider_status(self) -> ProviderStatusDTO: ...

    async def list_provider_models(self) -> ProviderModelsDTO: ...

    async def update_provider_model(
        self,
        *,
        config_dir: str,
        model: str,
        reasoning_effort: str = "auto",
    ) -> ProviderStatusDTO: ...


@dataclass(frozen=True)
class ModelScreenData:
    provider: str
    current_model: str
    current_reasoning_effort: str
    models: tuple[str, ...]
    reasoning_efforts: tuple[str, ...]
    enumeration_error: str | None = None


@dataclass(frozen=True)
class ModelSelection:
    model: str
    reasoning_effort: str


def provider_config_dir() -> str:
    """Return the config directory shared by the local TUI and API service."""
    return str(Path(ProviderConfigStore().path).parent)


def supported_reasoning_efforts(capabilities: dict) -> tuple[str, ...]:
    """Keep only canonical effort values, in stable UI order."""
    declared = capabilities.get("reasoning_efforts")
    if not isinstance(declared, (list, tuple)):
        return ("auto",)
    efforts = tuple(value for value in REASONING_EFFORTS if value in declared)
    return efforts or ("auto",)


async def load_model_screen_data(client: ModelAPI) -> ModelScreenData:
    """Load status first so model enumeration can fail without blocking manual input."""
    status = await client.get_provider_status()
    provider = status.provider
    current_model = status.model
    capabilities = status.capabilities
    models: tuple[str, ...] = ()
    enumeration_error: str | None = None

    try:
        response = await client.list_provider_models()
    except Exception as exc:  # noqa: BLE001 - degraded manual entry is intentional
        enumeration_error = str(exc) or exc.__class__.__name__
    else:
        provider = response.provider or provider
        current_model = response.current_model or current_model
        capabilities = response.capabilities or capabilities
        models = tuple(dict.fromkeys(model for model in response.models if model.strip()))
        if current_model and current_model not in models:
            models = (current_model, *models)

    efforts = supported_reasoning_efforts(capabilities)
    current_effort = status.reasoning_effort
    if current_effort not in efforts:
        current_effort = efforts[0]
    return ModelScreenData(
        provider=provider,
        current_model=current_model,
        current_reasoning_effort=current_effort,
        models=models,
        reasoning_efforts=efforts,
        enumeration_error=enumeration_error,
    )


async def save_model_selection(
    client: ModelAPI,
    *,
    config_dir: str,
    screen_data: ModelScreenData,
    selection: ModelSelection,
) -> ProviderStatusDTO:
    """Validate a UI selection and persist it through the hot-reload API."""
    model = selection.model.strip()
    if not model:
        raise ValueError("Model ID cannot be empty")
    if len(model) > 200:
        raise ValueError("Model ID cannot exceed 200 characters")
    if selection.reasoning_effort not in screen_data.reasoning_efforts:
        raise ValueError(
            f"Unsupported reasoning effort: {selection.reasoning_effort}"
        )
    return await client.update_provider_model(
        config_dir=config_dir,
        model=model,
        reasoning_effort=selection.reasoning_effort,
    )
