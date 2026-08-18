"""Authenticated local provider runtime control plane."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from personal_ai_os.common.models import ModelError
from personal_ai_os.model_gateway import (
    ProviderBusyError,
    ProviderConfigBoundaryError,
    ProviderReloadError,
)

from ..deps import get_services, resolve_user
from ..schemas import ProviderModelUpdateBody, ProviderReloadBody

router = APIRouter(prefix="/v1/provider", tags=["provider"])


def _runtime(services):  # noqa: ANN001, ANN202
    runtime = services.provider_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Provider runtime is not wired up")
    return runtime


def _control_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProviderBusyError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ProviderConfigBoundaryError):
        return HTTPException(status_code=412, detail=str(exc))
    if isinstance(exc, ProviderReloadError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Provider operation failed")


@router.get("")
async def get_provider_status(
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    del user
    return _runtime(services).status.to_dict()


@router.post("/reload")
async def reload_provider(
    body: ProviderReloadBody,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    del user
    runtime = _runtime(services)
    try:
        runtime.assert_shared_config_dir(body.config_dir)
        return (await runtime.reload()).to_dict()
    except (ProviderBusyError, ProviderConfigBoundaryError, ProviderReloadError) as exc:
        raise _control_error(exc) from exc


@router.get("/models")
async def list_provider_models(
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    del user
    runtime = _runtime(services)
    try:
        models = await runtime.list_models()
    except ModelError as exc:
        raise HTTPException(
            status_code=502, detail="Provider model enumeration failed"
        ) from exc
    return {
        "provider": runtime.status.provider,
        "current_model": runtime.status.model,
        "models": models,
        "capabilities": runtime.status.capabilities.to_dict(),
    }


@router.put("/model")
async def update_provider_model(
    body: ProviderModelUpdateBody,
    user=Depends(resolve_user),
    services=Depends(get_services),
) -> dict:
    del user
    runtime = _runtime(services)
    try:
        runtime.assert_shared_config_dir(body.config_dir)
        status = await runtime.update_model(
            model=body.model,
            reasoning_effort=body.reasoning_effort,
        )
        return status.to_dict()
    except (ProviderBusyError, ProviderConfigBoundaryError, ProviderReloadError) as exc:
        raise _control_error(exc) from exc
