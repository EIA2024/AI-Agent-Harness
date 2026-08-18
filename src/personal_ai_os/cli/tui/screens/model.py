"""Modal model and reasoning-effort selector for the TUI."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from personal_ai_os.cli.sanitize import sanitize_text
from personal_ai_os.cli.tui.model_flow import ModelScreenData, ModelSelection


def _safe_text(label: str, value: str) -> Text:
    return Text.assemble((label, "bold"), sanitize_text(value))


class ModelScreen(ModalScreen[ModelSelection | None]):
    """Collect a model ID and one of the provider-declared reasoning efforts."""

    CSS = """
    #model-panel {
        width: 80%;
        height: auto;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #model-title { text-style: bold; margin-bottom: 1; }
    #model-hint { color: $warning; margin: 1 0; }
    #model-error { color: $error; }
    #model-actions { margin-top: 1; height: auto; }
    #model-panel Select, #model-panel Input { margin-bottom: 1; }
    """

    BINDINGS = [
        ("ctrl+s", "save", "Save"),
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, data: ModelScreenData) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        with Vertical(id="model-panel"):
            yield Static("Model and reasoning", id="model-title")
            yield Static(_safe_text("Provider: ", self.data.provider), id="model-provider")
            yield Static(
                _safe_text("Current model: ", self.data.current_model),
                id="model-current",
            )

            if self.data.models:
                yield Static("Available models")
                yield Select(
                    [
                        (Text(sanitize_text(model)), model)
                        for model in self.data.models
                    ],
                    value=(
                        self.data.current_model
                        if self.data.current_model in self.data.models
                        else self.data.models[0]
                    ),
                    allow_blank=False,
                    id="model-select",
                )
            else:
                reason = sanitize_text(
                    self.data.enumeration_error or "model enumeration is unavailable"
                )
                yield Static(
                    Text.assemble(
                        ("Automatic model enumeration failed: ", "bold"),
                        reason,
                    ),
                    id="model-hint",
                )

            yield Input(
                placeholder=(
                    "Manual model ID (optional)"
                    if self.data.models
                    else "Manual model ID"
                ),
                id="model-manual",
                max_length=200,
            )
            yield Static("Reasoning effort")
            model_decides = self.data.reasoning_efforts == ("auto",)
            yield Select(
                [
                    (
                        "auto（由模型决定）"
                        if effort == "auto" and model_decides
                        else effort,
                        effort,
                    )
                    for effort in self.data.reasoning_efforts
                ],
                value=self.data.current_reasoning_effort,
                allow_blank=False,
                id="reasoning-select",
            )
            yield Static("", id="model-error")
            with Horizontal(id="model-actions"):
                yield Button("Save", id="model-save", variant="primary")
                yield Button("Cancel", id="model-cancel")

    def on_mount(self) -> None:
        target = "#model-select" if self.data.models else "#model-manual"
        self.query_one(target).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "model-save":
            self.action_save()
        elif event.button.id == "model-cancel":
            self.action_cancel()

    def action_save(self) -> None:
        manual_model = self.query_one("#model-manual", Input).value.strip()
        if manual_model:
            model = manual_model
        elif self.data.models:
            model = str(self.query_one("#model-select", Select).value)
        else:
            self.query_one("#model-error", Static).update("Enter a model ID")
            return

        reasoning_effort = str(self.query_one("#reasoning-select", Select).value)
        self.dismiss(
            ModelSelection(model=model, reasoning_effort=reasoning_effort)
        )

    def action_cancel(self) -> None:
        self.dismiss(None)
