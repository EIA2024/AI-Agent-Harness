"""Non-blocking provider profile configuration screen."""

from __future__ import annotations

from dataclasses import dataclass, field

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from personal_ai_os.model_gateway import KNOWN_PROVIDERS, ProviderProfile

_ADD_PROFILE = "__add_profile__"
_CUSTOM_PROVIDER = "custom"


@dataclass(frozen=True)
class APIConfigRequest:
    action: str
    profile_name: str
    format: str = "openai"
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)


class APIConfigScreen(ModalScreen[APIConfigRequest | None]):
    """Collect provider configuration without terminal-blocking prompts."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        profiles: list[ProviderProfile],
        *,
        active_name: str | None,
        runtime_summary: str,
    ) -> None:
        super().__init__()
        self._profiles = {profile.name: profile for profile in profiles}
        self._active_name = active_name
        self._runtime_summary = runtime_summary

    def compose(self) -> ComposeResult:
        selected = (
            self._active_name
            if self._active_name in self._profiles
            else next(iter(self._profiles), _ADD_PROFILE)
        )
        profile_options = [
            (
                f"{'active · ' if name == self._active_name else ''}{name}",
                name,
            )
            for name in self._profiles
        ]
        profile_options.append(("Add profile…", _ADD_PROFILE))

        with Vertical(id="api-panel"):
            yield Static("[bold]LLM provider configuration[/]", id="api-title")
            yield Static(self._runtime_summary, id="api-runtime")
            yield Label("Saved profile")
            yield Select(
                profile_options,
                value=selected,
                allow_blank=False,
                id="api-profile",
            )
            yield Label("Provider preset")
            yield Select(
                [(name.title(), name) for name in KNOWN_PROVIDERS]
                + [("Custom", _CUSTOM_PROVIDER)],
                value="deepseek",
                allow_blank=False,
                id="api-provider",
            )
            yield Label("Profile name")
            yield Input(id="api-profile-name")
            yield Label("Request format")
            yield Select(
                [
                    ("OpenAI compatible", "openai"),
                    ("Anthropic", "anthropic"),
                ],
                value="openai",
                allow_blank=False,
                id="api-format",
            )
            yield Label("Base URL")
            yield Input(id="api-base-url")
            yield Label("Default model")
            yield Input(id="api-model")
            yield Label("API key (blank keeps the existing key)")
            yield Input(password=True, id="api-key")
            yield Static("", id="api-key-state")
            yield Static("", id="api-error")
            with Horizontal(id="api-actions"):
                yield Button("Activate", id="api-activate", variant="primary")
                yield Button("Save & activate", id="api-save", variant="success")
                yield Button("Cancel", id="api-cancel")

    def on_mount(self) -> None:
        selected = str(self.query_one("#api-profile", Select).value)
        if selected == _ADD_PROFILE:
            self._load_preset("deepseek")
        else:
            self._load_profile(selected)
        self.query_one("#api-key", Input).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "api-profile":
            name = str(event.value)
            if name == _ADD_PROFILE:
                self._load_preset("deepseek")
            elif name in self._profiles:
                self._load_profile(name)
        elif event.select.id == "api-provider":
            provider = str(event.value)
            if self._is_adding and provider in KNOWN_PROVIDERS:
                self._load_preset(provider)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "api-cancel":
            self.action_cancel()
        elif event.button.id == "api-activate":
            self._activate()
        elif event.button.id == "api-save":
            self._save()

    @property
    def _is_adding(self) -> bool:
        return self.query_one("#api-profile", Select).value == _ADD_PROFILE

    def _load_profile(self, name: str) -> None:
        profile = self._profiles[name]
        provider = self._provider_for(profile)
        self.query_one("#api-provider", Select).value = provider
        self.query_one("#api-profile-name", Input).value = profile.name
        self.query_one("#api-profile-name", Input).disabled = True
        self.query_one("#api-format", Select).value = profile.format
        self.query_one("#api-base-url", Input).value = profile.base_url
        self.query_one("#api-model", Input).value = profile.model
        self.query_one("#api-key", Input).value = ""
        configured = bool(profile.api_key or profile.secret_ref)
        self.query_one("#api-key-state", Static).update(
            "API key: configured" if configured else "API key: missing"
        )
        self.query_one("#api-activate", Button).disabled = (
            name == self._active_name
        )
        self._set_error("")

    def _load_preset(self, provider: str) -> None:
        preset = KNOWN_PROVIDERS[provider]
        self.query_one("#api-provider", Select).value = provider
        name_input = self.query_one("#api-profile-name", Input)
        name_input.disabled = False
        name_input.value = provider
        self.query_one("#api-format", Select).value = preset["format"]
        self.query_one("#api-base-url", Input).value = preset["base_url"]
        self.query_one("#api-model", Input).value = preset["model"]
        self.query_one("#api-key", Input).value = ""
        self.query_one("#api-key-state", Static).update("API key: required")
        self.query_one("#api-activate", Button).disabled = True
        self._set_error("")

    def _provider_for(self, profile: ProviderProfile) -> str:
        for name, preset in KNOWN_PROVIDERS.items():
            if (
                profile.format == preset["format"]
                and profile.base_url == preset["base_url"]
            ):
                return name
        return _CUSTOM_PROVIDER

    def _activate(self) -> None:
        selected = str(self.query_one("#api-profile", Select).value)
        if selected in self._profiles:
            self.dismiss(APIConfigRequest("activate", selected))

    def _save(self) -> None:
        name = self.query_one("#api-profile-name", Input).value.strip()
        api_key = self.query_one("#api-key", Input).value
        if not name:
            self._set_error("Profile name is required")
            return
        if self._is_adding and name in self._profiles:
            self._set_error("That profile already exists; select it to update")
            return
        if self._is_adding and not api_key:
            self._set_error("API key is required")
            return
        self.dismiss(
            APIConfigRequest(
                "add" if self._is_adding else "update",
                name,
                format=str(self.query_one("#api-format", Select).value),
                base_url=self.query_one("#api-base-url", Input).value.strip(),
                model=self.query_one("#api-model", Input).value.strip(),
                api_key=api_key,
            )
        )

    def _set_error(self, message: str) -> None:
        self.query_one("#api-error", Static).update(message)

    def action_cancel(self) -> None:
        self.dismiss(None)
