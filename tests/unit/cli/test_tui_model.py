"""Focused tests for the independent TUI model-selection flow."""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App
from textual.widgets import Input, Select, Static

from personal_ai_os.cli.api.dto import ProviderModelsDTO, ProviderStatusDTO
from personal_ai_os.cli.domain.state import TranscriptCell
from personal_ai_os.cli.tui.app import PersonalAIApp
from personal_ai_os.cli.tui.command_registry import lookup
from personal_ai_os.cli.tui.model_flow import (
    ModelScreenData,
    ModelSelection,
    load_model_screen_data,
    save_model_selection,
    supported_reasoning_efforts,
)
from personal_ai_os.cli.tui.screens.model import ModelScreen
from personal_ai_os.cli.tui.widgets.composer import Composer
from personal_ai_os.model_gateway import ProviderConfigStore


class ModelFakeClient:
    base_url = "http://localhost:8000"

    def __init__(
        self,
        *,
        status: ProviderStatusDTO,
        models: ProviderModelsDTO | None = None,
        enumeration_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.models = models
        self.enumeration_error = enumeration_error
        self.updates: list[dict[str, str]] = []

    async def get_provider_status(self) -> ProviderStatusDTO:
        return self.status

    async def list_provider_models(self) -> ProviderModelsDTO:
        if self.enumeration_error is not None:
            raise self.enumeration_error
        assert self.models is not None
        return self.models

    async def update_provider_model(
        self,
        *,
        config_dir: str,
        model: str,
        reasoning_effort: str = "auto",
    ) -> ProviderStatusDTO:
        self.updates.append(
            {
                "config_dir": config_dir,
                "model": model,
                "reasoning_effort": reasoning_effort,
            }
        )
        return ProviderStatusDTO(
            mode="provider",
            profile="default",
            provider=self.status.provider,
            model=model,
            reasoning_effort=reasoning_effort,
            capabilities=self.status.capabilities,
        )

    async def aclose(self) -> None:
        pass


class ModelScreenHost(App):
    def __init__(self, data: ModelScreenData) -> None:
        super().__init__()
        self.data = data
        self.result: ModelSelection | None = None

    def on_mount(self) -> None:
        self.push_screen(ModelScreen(self.data), callback=self._capture)

    def _capture(self, result: ModelSelection | None) -> None:
        self.result = result


def _status(
    *,
    provider: str = "openai",
    model: str = "o3",
    effort: str = "medium",
    efforts: list[str] | None = None,
) -> ProviderStatusDTO:
    return ProviderStatusDTO(
        mode="provider",
        provider=provider,
        model=model,
        reasoning_effort=effort,
        capabilities={"reasoning_efforts": efforts or ["auto", "low", "medium", "high"]},
    )


def test_reasoning_efforts_are_filtered_and_ordered():
    assert supported_reasoning_efforts(
        {"reasoning_efforts": ["high", "invalid", "auto", "high"]}
    ) == ("auto", "high")
    assert supported_reasoning_efforts({}) == ("auto",)


async def test_loads_enumerated_models_and_declared_efforts():
    client = ModelFakeClient(
        status=_status(),
        models=ProviderModelsDTO(
            provider="openai",
            current_model="o3",
            models=["o3-mini", "o3"],
            capabilities={"reasoning_efforts": ["auto", "low", "high"]},
        ),
    )

    data = await load_model_screen_data(client)

    assert data.provider == "openai"
    assert data.current_model == "o3"
    assert data.models == ("o3-mini", "o3")
    assert data.reasoning_efforts == ("auto", "low", "high")
    assert data.current_reasoning_effort == "auto"
    assert data.enumeration_error is None


async def test_enumeration_failure_keeps_status_for_manual_fallback():
    client = ModelFakeClient(
        status=_status(
            provider="deepseek",
            model="deepseek-chat",
            effort="auto",
            efforts=["auto"],
        ),
        enumeration_error=RuntimeError("provider unavailable"),
    )

    data = await load_model_screen_data(client)

    assert data.provider == "deepseek"
    assert data.current_model == "deepseek-chat"
    assert data.models == ()
    assert data.reasoning_efforts == ("auto",)
    assert data.enumeration_error == "provider unavailable"


async def test_save_uses_hot_reload_api_without_session_operations():
    client = ModelFakeClient(status=_status())
    data = ModelScreenData(
        provider="openai",
        current_model="o3",
        current_reasoning_effort="medium",
        models=("o3", "o3-mini"),
        reasoning_efforts=("auto", "low", "medium", "high"),
    )

    updated = await save_model_selection(
        client,
        config_dir="/shared",
        screen_data=data,
        selection=ModelSelection(" o3-mini ", "high"),
    )

    assert updated.model == "o3-mini"
    assert client.updates == [
        {
            "config_dir": "/shared",
            "model": "o3-mini",
            "reasoning_effort": "high",
        }
    ]


async def test_save_rejects_undeclared_effort():
    client = ModelFakeClient(status=_status())
    data = ModelScreenData(
        provider="deepseek",
        current_model="deepseek-chat",
        current_reasoning_effort="auto",
        models=("deepseek-chat",),
        reasoning_efforts=("auto",),
    )

    with pytest.raises(ValueError, match="Unsupported reasoning effort"):
        await save_model_selection(
            client,
            config_dir="/shared",
            screen_data=data,
            selection=ModelSelection("deepseek-chat", "high"),
        )
    assert client.updates == []


async def test_deepseek_screen_shows_model_decides_and_only_auto():
    data = ModelScreenData(
        provider="deepseek",
        current_model="deepseek-chat",
        current_reasoning_effort="auto",
        models=("deepseek-chat", "deepseek-reasoner"),
        reasoning_efforts=("auto",),
    )
    app = ModelScreenHost(data)

    async with app.run_test() as pilot:
        await pilot.pause()
        reasoning = app.screen.query_one("#reasoning-select", Select)
        assert reasoning.value == "auto"
        assert reasoning._options == [("auto（由模型决定）", "auto")]


async def test_enumerated_screen_submits_selected_model_and_effort():
    data = ModelScreenData(
        provider="openai",
        current_model="o3",
        current_reasoning_effort="medium",
        models=("o3", "o3-mini"),
        reasoning_efforts=("auto", "low", "medium", "high"),
    )
    app = ModelScreenHost(data)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#model-select", Select).value = "o3-mini"
        app.screen.query_one("#reasoning-select", Select).value = "high"
        app.screen.action_save()
        await pilot.pause()
        await asyncio.sleep(0.05)

    assert app.result == ModelSelection("o3-mini", "high")


async def test_manual_fallback_submits_model_id_and_sanitizes_error():
    data = ModelScreenData(
        provider="custom",
        current_model="old-model",
        current_reasoning_effort="auto",
        models=(),
        reasoning_efforts=("auto",),
        enumeration_error="\x1b[31mfailed\x1b[0m",
    )
    app = ModelScreenHost(data)

    async with app.run_test() as pilot:
        await pilot.pause()
        hint = app.screen.query_one("#model-hint", Static)
        assert "\x1b" not in str(hint.render())
        assert "failed" in str(hint.render())
        app.screen.query_one("#model-manual", Input).value = "custom-reasoner"
        app.screen.action_save()
        await pilot.pause()
        await asyncio.sleep(0.05)

    assert app.result == ModelSelection("custom-reasoner", "auto")


async def test_app_model_command_saves_applies_status_and_preserves_session(
    tmp_path,
):
    client = ModelFakeClient(
        status=_status(),
        models=ProviderModelsDTO(
            provider="openai",
            current_model="o3",
            models=["o3", "o3-mini"],
            capabilities={"reasoning_efforts": ["auto", "low", "medium", "high"]},
        ),
    )
    store = ProviderConfigStore(path=tmp_path / "config" / "profiles.json")
    app = PersonalAIApp(client=client, provider_store=store)

    async with app.run_test() as pilot:
        assert app.controller is not None
        app.controller.state.session_id = "session-1"
        app.controller.state.transcript.append(
            TranscriptCell(kind="assistant", text="existing transcript")
        )

        composer = app.query_one("#composer", Composer)
        composer.text = "/model"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ModelScreen)

        app.screen.query_one("#model-select", Select).value = "o3-mini"
        app.screen.query_one("#reasoning-select", Select).value = "high"
        app.screen.action_save()
        await pilot.pause()
        await asyncio.sleep(0.05)

        assert app.controller.state.session_id == "session-1"
        assert app.controller.state.transcript[0].text == "existing transcript"
        assert app.controller.state.provider_status.model == "o3-mini"
        assert app.controller.state.provider_status.reasoning_effort == "high"

    assert lookup("model").action == "cmd_model"
    assert client.updates == [
        {
            "config_dir": str(store.path.parent),
            "model": "o3-mini",
            "reasoning_effort": "high",
        }
    ]


async def test_app_model_command_rejects_remote_api(tmp_path):
    client = ModelFakeClient(status=_status())
    client.base_url = "https://remote.example"
    app = PersonalAIApp(
        client=client,
        provider_store=ProviderConfigStore(
            path=tmp_path / "config" / "profiles.json"
        ),
    )

    async with app.run_test() as pilot:
        await app.cmd_model("")
        await pilot.pause()
        assert not isinstance(app.screen, ModelScreen)

    assert client.updates == []
