"""Approval modal screen (CLI v2, T31/T32)."""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class ApprovalScreen(ModalScreen[tuple[str, dict[str, Any] | None]]):
    """Shows the pending approval and lets the user decide."""

    BINDINGS = [
        ("a", "choose_approve", "Approve once"),
        ("r", "choose_reject", "Reject"),
        ("e", "choose_edit", "Edit arguments"),
        ("c", "choose_cancel", "Cancel run"),
        ("escape", "choose_dismiss", "Dismiss"),
    ]

    def __init__(self, approval: dict[str, Any]) -> None:
        super().__init__()
        self.approval = approval

    def on_mount(self) -> None:
        self.query_one("#btn-approve", Button).focus()

    def compose(self) -> ComposeResult:
        risk = self.approval.get("risk_level", 0)
        tool = self.approval.get("tool_name", "?")
        summary = self.approval.get("action_summary", "")
        args = self.approval.get("arguments_preview", {})
        with Vertical(classes="approval-panel"):
            yield Static(f"[bold red]! Approval required · R{risk}[/]", id="approval-title")
            yield Static(f"Tool: {tool}")
            yield Static(f"Action: {summary or '(none)'}")
            yield Static(f"Args: {json.dumps(args, ensure_ascii=False)[:400]}")
            yield Static("", id="edit-hint")
            yield Input(placeholder='Edited args (JSON) — used with Edit', id="edit-input")
            with Horizontal(id="approval-actions"):
                yield Button("Approve once", id="btn-approve", variant="success")
                yield Button("Edit", id="btn-edit", variant="primary")
                yield Button("Reject", id="btn-reject", variant="warning")
                yield Button("Cancel run", id="btn-cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "btn-approve":
            self.choose_approve()
        elif button_id == "btn-edit":
            self.choose_edit()
        elif button_id == "btn-reject":
            self.choose_reject()
        elif button_id == "btn-cancel":
            self.choose_cancel()

    def choose_approve(self) -> None:
        self.dismiss(("approve", None))

    def choose_reject(self) -> None:
        self.dismiss(("reject", None))

    def choose_cancel(self) -> None:
        self.dismiss(("cancel", None))

    def choose_edit(self) -> None:
        raw = self.query_one("#edit-input", Input).value.strip()
        if not raw:
            self.dismiss(("edit", {}))
            return
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            self.query_one("#edit-hint", Static).update("[error]invalid JSON — fix and press Edit again[/]")
            return
        self.dismiss(("edit", parsed))

    def choose_dismiss(self) -> None:
        self.dismiss(None)
