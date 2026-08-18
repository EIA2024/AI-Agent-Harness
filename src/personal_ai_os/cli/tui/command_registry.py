"""TUI slash-command registry (CLI v2, T34/T35).

One metadata source for the command palette, ``--help`` and tests — never
scattered ``if text.startswith("/...")`` chains. Each command maps to an
action method on the app.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    action: str


REGISTRY: tuple[CommandSpec, ...] = (
    CommandSpec("help", "Show key bindings and commands", "cmd_help"),
    CommandSpec("status", "Show session, model, run and connection state", "cmd_status"),
    CommandSpec("api", "Configure or switch LLM provider profiles", "cmd_api"),
    CommandSpec("model", "Select the LLM model and reasoning effort", "cmd_model"),
    CommandSpec("context", "Show context summary", "cmd_context"),
    CommandSpec("memory", "List / search memories", "cmd_memory"),
    CommandSpec("tools", "List server-registered tools", "cmd_tools"),
    CommandSpec("approvals", "List pending approvals", "cmd_approvals"),
    CommandSpec("cancel", "Cancel the active run", "action_interrupt"),
    CommandSpec("clear", "Clear the visible transcript", "action_clear_view"),
    CommandSpec("new", "Start a new session", "cmd_new"),
    CommandSpec("exit", "Quit", "cmd_exit"),
)


def lookup(name: str) -> CommandSpec | None:
    for spec in REGISTRY:
        if spec.name == name:
            return spec
    return None


def list_commands() -> list[CommandSpec]:
    return list(REGISTRY)
