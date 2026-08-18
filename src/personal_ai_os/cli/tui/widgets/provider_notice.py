"""Persistent provider notice and Echo demo presentation helpers."""

from __future__ import annotations

from textual.widgets import Static

from personal_ai_os.cli.domain.state import AppState
from personal_ai_os.cli.sanitize import strip_control_sequences

ECHO_CONFIGURATION_NOTICE = (
    "Echo demo mode: no LLM provider/API key is configured. "
    "Type /api to configure one."
)
ECHO_RESPONSE_PREFIX = "[Echo demo response]"


def provider_notice_text(state: AppState) -> str | None:
    """Return the persistent notice required by the API-reported provider mode."""
    if (
        state.api_connection_state.value == "connected"
        and state.provider_status.is_echo
    ):
        return ECHO_CONFIGURATION_NOTICE
    return None


def echo_demo_response_text(text: str) -> str:
    """Mark an Echo response as demonstration output and sanitize its content."""
    content = strip_control_sequences(text)
    if content.startswith(ECHO_RESPONSE_PREFIX):
        return content
    return f"{ECHO_RESPONSE_PREFIX} {content}"


class ProviderNotice(Static):
    """A reusable banner that remains visible while the API reports Echo mode."""

    def render_state(self, state: AppState) -> None:
        notice = provider_notice_text(state)
        self.update(notice or "")
        self.display = notice is not None
