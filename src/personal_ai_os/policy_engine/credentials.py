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
    """Resolves credential scopes to secrets and injects them into arguments.

    ``source`` supports two layouts:
      * flat ``{scope: secret}`` — single-user / legacy; any owner may use it.
      * owner-scoped ``{owner_id: {scope: secret}}`` — P0-007: a secret is bound
        to its owner, and a different owner can never resolve it. In
        owner-scoped mode the process-global env fallback is DISABLED so a
        secret configured for one user is never auto-granted to another.
    """

    def __init__(self, *, source: dict | None = None):
        self.source = dict(source or {})

    # -- resolution --------------------------------------------------------

    def _env_key(self, scope: str) -> str:
        """Map a scope like ``github.token`` to an env var like ``GITHUB_TOKEN``."""
        return scope.replace(".", "_").replace("-", "_").upper()

    def _is_owner_scoped(self) -> bool:
        return any(isinstance(value, dict) for value in self.source.values())

    def resolve_secret(self, scope: str, owner_id: Any = None) -> str | None:
        """Return the raw secret for a scope + owner, or None if not configured."""
        if self._is_owner_scoped():
            key = str(owner_id) if owner_id is not None else None
            if key is not None and key in self.source:
                return (self.source[key] or {}).get(scope)
            return None  # no global fallback in owner-scoped mode (P0-007)
        if scope in self.source:
            return self.source[scope]
        return os.environ.get(self._env_key(scope))

    def secret_ref(self, scope: str, owner_id: Any = None) -> str | None:
        """Return a *reference* to the secret (never the value), or None."""
        if self._is_owner_scoped():
            key = str(owner_id) if owner_id is not None else None
            if key is not None and key in self.source and scope in (self.source[key] or {}):
                return f"owner:{key}:{scope}"
            return None
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
            secret = self.resolve_secret(scope, owner_id=owner_id)
            if secret is None:
                raise AuthError(
                    f"Credential for scope {scope!r} is not configured for this owner "
                    f"(expected owner-scoped secret or env var {self._env_key(scope)}).",
                    detail={"tool": tool.name, "scope": scope},
                )
            # ``token`` envelope => LogSanitizer.sanitize_dict redacts the value.
            secrets[scope] = {"token": secret}
        if secrets:
            injected["_secrets"] = secrets
        return injected
