"""Credential broker — secret injection so the agent never handles raw secrets.

MVP stores secrets in ``os.environ`` (or a ``source`` dict for tests). The
broker resolves each scope in ``tool.credential_scope`` and injects the value
into the tool arguments under the hidden ``_secrets`` field — never into
``content`` or other LLM-visible fields.

The injected payload uses a ``{"token": ...}`` envelope per scope so the shared
``LogSanitizer.sanitize_dict`` (which redacts keys named ``token``/``secret``/…)
strips the raw value from anything that reaches logs or audit.
"""

from __future__ import annotations

import os
from typing import Any

from ..common.models import AuthError, ToolDescriptor


class CredentialBroker:
    """Resolves credential scopes to secrets and injects them into arguments."""

    def __init__(self, *, source: dict | None = None):
        #: scope -> secret. Overrides environment variables (used by tests).
        self.source = dict(source or {})

    # -- resolution --------------------------------------------------------

    def _env_key(self, scope: str) -> str:
        """Map a scope like ``github.token`` to an env var like ``GITHUB_TOKEN``."""
        return scope.replace(".", "_").replace("-", "_").upper()

    def resolve_secret(self, scope: str) -> str | None:
        """Return the raw secret for a scope, or None if it is not configured."""
        if scope in self.source:
            return self.source[scope]
        return os.environ.get(self._env_key(scope))

    def secret_ref(self, scope: str) -> str | None:
        """Return a *reference* to the secret (never the value), or None."""
        if scope in self.source:
            return f"source:{scope}"
        if os.environ.get(self._env_key(scope)):
            return f"env:{self._env_key(scope)}"
        return None

    # -- injection ---------------------------------------------------------

    async def inject(self, tool: ToolDescriptor, arguments: dict, owner_id: Any) -> dict:
        """Return a copy of ``arguments`` with secrets injected under ``_secrets``.

        Raises :class:`AuthError` (recoverable) if a required scope is missing.
        The input dict is never mutated.
        """
        injected = dict(arguments)
        secrets: dict[str, dict] = {}
        for scope in tool.credential_scope or []:
            secret = self.resolve_secret(scope)
            if secret is None:
                raise AuthError(
                    f"Credential for scope {scope!r} is not configured "
                    f"(expected env var {self._env_key(scope)}).",
                    detail={"tool": tool.name, "scope": scope},
                )
            # ``token`` envelope => LogSanitizer.sanitize_dict redacts the value.
            secrets[scope] = {"token": secret}
        if secrets:
            injected["_secrets"] = secrets
        return injected
