"""Focused tests for the TUI /api provider configuration flow."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from textual.widgets import Input, Select

from personal_ai_os.cli.tui.app import PersonalAIApp
from personal_ai_os.cli.tui.screens.api_config import APIConfigScreen
from personal_ai_os.cli.tui.widgets.composer import Composer
from personal_ai_os.model_gateway import ProviderConfigStore, ProviderProfile


class _MemoryVault:
    def __init__(self, *, writable: bool = True) -> None:
        self.values: dict[str, str] = {}
        self.writable = writable

    def set(self, name: str, key: str) -> bool:
        if not self.writable:
            return False
        self.values[name] = key
        return True

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def delete(self, name: str) -> bool:
        self.values.pop(name, None)
        return True


class _ProviderClient:
    base_url = "http://localhost:8000"

    def __init__(self, *, reload_error: Exception | None = None) -> None:
        self.reload_error = reload_error
        self.reload_dirs: list[str] = []

    async def get_provider_status(self):
        return SimpleNamespace(
            mode="echo",
            profile=None,
            provider="echo",
            model="echo",
            reasoning_effort="auto",
            capabilities={},
        )

    async def reload_provider(self, config_dir: str):
        self.reload_dirs.append(config_dir)
        if self.reload_error is not None:
            raise self.reload_error
        return SimpleNamespace(
            mode="provider",
            profile="active",
            provider="deepseek",
            model="deepseek-chat",
            reasoning_effort="auto",
            capabilities={"reasoning_efforts": ["auto"]},
        )

    async def create_session(self, channel: str = "cli", **extra):
        return SimpleNamespace(id="s1", channel=channel)

    async def send_message(self, session_id: str, text: str):
        return SimpleNamespace(run_id="r1", status="running", message_id="m1")

    async def stream_run(self, run_id: str):
        await asyncio.Event().wait()
        yield  # pragma: no cover

    async def cancel_run(self, run_id: str):
        return SimpleNamespace(id=run_id, status="cancelled")

    async def aclose(self) -> None:
        pass


def _store(tmp_path, *, vault: _MemoryVault | None = None) -> ProviderConfigStore:
    return ProviderConfigStore(
        path=tmp_path / "config" / "profiles.json",
        vault=vault or _MemoryVault(),
    )


async def _open_api_screen(app: PersonalAIApp, pilot) -> APIConfigScreen:  # noqa: ANN001
    composer = app.query_one("#composer", Composer)
    composer.text = "/api"
    await pilot.press("enter")
    for _ in range(50):
        await pilot.pause()
        if isinstance(app.screen, APIConfigScreen):
            return app.screen
    raise AssertionError("/api screen did not open")


async def _wait_closed(app: PersonalAIApp, pilot) -> None:  # noqa: ANN001
    for _ in range(100):
        await pilot.pause()
        if not isinstance(app.screen, APIConfigScreen):
            return
        await asyncio.sleep(0.005)
    raise AssertionError("/api screen did not close")


async def test_first_api_setup_defaults_to_deepseek_and_masks_key(tmp_path):
    store = _store(tmp_path)
    client = _ProviderClient()
    app = PersonalAIApp(client=client, provider_store=store)

    async with app.run_test() as pilot:
        screen = await _open_api_screen(app, pilot)
        assert screen.query_one("#api-provider", Select).value == "deepseek"
        assert (
            screen.query_one("#api-base-url", Input).value
            == "https://api.deepseek.com/v1"
        )
        assert screen.query_one("#api-model", Input).value == "deepseek-chat"
        key_input = screen.query_one("#api-key", Input)
        assert key_input.password is True

        secret = "sk-deepseek-render-secret"
        key_input.value = secret
        await pilot.pause()
        rendered = " ".join(str(widget.render()) for widget in screen.query("*"))
        assert secret not in rendered

        screen.query_one("#api-save").press()
        await _wait_closed(app, pilot)

    active = store.get_active()
    assert active is not None
    assert active.name == "deepseek"
    assert active.model == "deepseek-chat"
    assert active.api_key == secret
    assert secret not in store.path.read_text(encoding="utf-8")
    assert client.reload_dirs == [str(store.path.parent)]


async def test_api_switch_and_update_existing_profiles(tmp_path):
    store = _store(tmp_path)
    store.add(
        ProviderProfile(
            name="first",
            format="openai",
            api_key="first-key",
            base_url="https://first.example/v1",
            model="first-model",
        )
    )
    store.add(
        ProviderProfile(
            name="second",
            format="openai",
            api_key="second-key",
            base_url="https://second.example/v1",
            model="second-model",
        ),
        activate=False,
    )
    client = _ProviderClient()
    app = PersonalAIApp(client=client, provider_store=store)

    async with app.run_test() as pilot:
        screen = await _open_api_screen(app, pilot)
        screen.query_one("#api-profile", Select).value = "second"
        await pilot.pause()
        screen.query_one("#api-activate").press()
        await _wait_closed(app, pilot)
        assert store.get_active_name() == "second"

        screen = await _open_api_screen(app, pilot)
        screen.query_one("#api-base-url", Input).value = "https://updated.example/v1"
        screen.query_one("#api-key", Input).value = "updated-key"
        screen.query_one("#api-save").press()
        await _wait_closed(app, pilot)

    updated = store.get("second")
    assert updated is not None
    assert updated.base_url == "https://updated.example/v1"
    assert updated.api_key == "updated-key"
    assert len(client.reload_dirs) == 2


async def test_api_cancel_preserves_configuration(tmp_path):
    store = _store(tmp_path)
    store.add(
        ProviderProfile(
            name="existing",
            format="openai",
            api_key="existing-key",
            base_url="https://existing.example/v1",
            model="existing-model",
        )
    )
    client = _ProviderClient()
    app = PersonalAIApp(client=client, provider_store=store)

    async with app.run_test() as pilot:
        screen = await _open_api_screen(app, pilot)
        screen.query_one("#api-base-url", Input).value = "https://changed.example/v1"
        screen.query_one("#api-cancel").press()
        await _wait_closed(app, pilot)

    assert store.get_active().base_url == "https://existing.example/v1"
    assert client.reload_dirs == []


async def test_invalid_key_reload_rolls_back_and_redacts_error(tmp_path, monkeypatch):
    secret = "invalid-key-must-not-leak"
    store = _store(tmp_path)
    client = _ProviderClient(reload_error=RuntimeError(f"invalid key: {secret}"))
    app = PersonalAIApp(client=client, provider_store=store)
    notices: list[str] = []
    monkeypatch.setattr(
        app,
        "notify",
        lambda message, **kwargs: notices.append(str(message)),
    )

    async with app.run_test() as pilot:
        screen = await _open_api_screen(app, pilot)
        screen.query_one("#api-key", Input).value = secret
        screen.query_one("#api-save").press()
        await _wait_closed(app, pilot)

    assert store.list_profiles() == []
    assert secret not in " ".join(notices)
    assert "***" in " ".join(notices)


async def test_failed_activation_restores_no_active_profile(tmp_path):
    store = _store(tmp_path)
    store.add(
        ProviderProfile(
            name="saved",
            format="openai",
            api_key="saved-key",
            base_url="https://saved.example/v1",
            model="saved-model",
        )
    )
    store.clear_active()
    client = _ProviderClient(reload_error=RuntimeError("invalid provider"))
    app = PersonalAIApp(client=client, provider_store=store)

    async with app.run_test() as pilot:
        screen = await _open_api_screen(app, pilot)
        screen.query_one("#api-activate").press()
        await _wait_closed(app, pilot)

    assert store.get_active_name() is None


async def test_keyring_failure_does_not_save_or_reload(tmp_path, monkeypatch):
    store = _store(tmp_path, vault=_MemoryVault(writable=False))
    client = _ProviderClient()
    app = PersonalAIApp(client=client, provider_store=store)
    notices: list[str] = []
    monkeypatch.setattr(
        app,
        "notify",
        lambda message, **kwargs: notices.append(str(message)),
    )

    async with app.run_test() as pilot:
        screen = await _open_api_screen(app, pilot)
        screen.query_one("#api-key", Input).value = "keyring-failure-secret"
        screen.query_one("#api-save").press()
        await _wait_closed(app, pilot)

    assert store.list_profiles() == []
    assert client.reload_dirs == []
    assert "keyring is unavailable" in " ".join(notices)


async def test_api_is_blocked_while_run_is_busy(tmp_path):
    store = _store(tmp_path)
    client = _ProviderClient()
    app = PersonalAIApp(client=client, provider_store=store)

    async with app.run_test() as pilot:
        composer = app.query_one("#composer", Composer)
        composer.text = "long run"
        await pilot.press("enter")
        await pilot.pause()
        assert app.controller is not None and app.controller.busy

        await app.cmd_api("")
        await pilot.pause()
        assert not isinstance(app.screen, APIConfigScreen)
        assert client.reload_dirs == []
