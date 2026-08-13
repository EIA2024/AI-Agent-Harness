"""In-memory Tool Registry.

Thread-safe registration / discovery / query of ``ToolDescriptor`` entries.
This is the single place the system learns *which* tools exist; the
:class:`ToolBroker` performs the actual execution.
"""

from __future__ import annotations

import inspect
import logging
import threading
from typing import TYPE_CHECKING

from personal_ai_os.common.models import ToolDescriptor

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def _is_awaitable(value) -> bool:
    return inspect.isawaitable(value)


# HTTP verbs that mutate remote state (P0-002 capability invariant).
_MUTATING_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _validate_capability_invariants(tool: ToolDescriptor) -> None:
    """Reject capability metadata that contradicts the tool's declared schema.

    Registration-time capability invariants (P0-002 / P1-013):
      * a read-only descriptor must not expose mutating HTTP verbs
      * destructive tools must be R4
      * external (remote) writes must be R3+
      * any side effect (even local) must be R2+
      * credential-scoped tools must be R1+
    """
    risk = tool.risk_level or 0
    if tool.source != "native" and tool.result_trust in {
        "trusted_user",
        "trusted_system",
        "trusted_tool",
    }:
        raise ValueError(
            f"capability invariant violated for {tool.name!r}: non-native source "
            "cannot self-assign a trusted result label"
        )
    if tool.destructive and risk < 4:
        raise ValueError(
            f"capability invariant violated for {tool.name!r}: destructive tool must be R4, got R{risk}"
        )
    if tool.external_write and risk < 3:
        raise ValueError(
            f"capability invariant violated for {tool.name!r}: external write must be R3+, got R{risk}"
        )
    if tool.side_effect and risk < 2:
        raise ValueError(
            f"capability invariant violated for {tool.name!r}: side-effecting tool must be R2+, got R{risk}"
        )
    if tool.credential_scope and risk < 1:
        raise ValueError(
            f"capability invariant violated for {tool.name!r}: credential-scoped tool must be R1+, got R{risk}"
        )
    if tool.side_effect or tool.external_write:
        return  # not claiming read-only; HTTP-method check below is irrelevant
    schema = tool.input_schema or {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    method_prop = properties.get("method") if isinstance(properties, dict) else None
    enum = method_prop.get("enum") if isinstance(method_prop, dict) else None
    if enum:
        mutating = _MUTATING_HTTP_METHODS.intersection(e.upper() for e in enum)
        if mutating:
            raise ValueError(
                f"capability invariant violated for {tool.name!r}: read-only metadata "
                f"(side_effect=False, external_write=False) but schema allows mutating "
                f"HTTP methods {sorted(mutating)}"
            )


async def _coerce_tools(value):
    """list_tools() is declared async on the Connector Protocol, but allow
    synchronous implementations too (FakeConnectors in tests)."""
    if _is_awaitable(value):
        return await value
    return value


class ToolRegistry:
    """Thread-safe in-memory registry of tool descriptors."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDescriptor] = {}
        # RLock so public methods (e.g. list_for_llm → search) may re-enter safely.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: ToolDescriptor) -> None:
        """Register a tool. Idempotent: re-registering the same name is a no-op
        that overwrites the previous descriptor and logs a warning.

        Raises ``ValueError`` when the descriptor's capability metadata
        contradicts its schema (P0-002 capability invariants).
        """
        _validate_capability_invariants(tool)
        with self._lock:
            existing = self._tools.get(tool.name)
            if existing is not None:
                logger.warning("Tool %r already registered; overwriting", tool.name)
            self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        with self._lock:
            self._tools.pop(name, None)

    async def register_connector(self, connector) -> None:
        """Register every tool exposed by a Connector (Protocol: list_tools async)."""
        tools = await _coerce_tools(connector.list_tools())
        for tool in tools:
            self.register(tool)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, name: str) -> ToolDescriptor | None:
        with self._lock:
            return self._tools.get(name)

    def list_all(self) -> list[ToolDescriptor]:
        with self._lock:
            return list(self._tools.values())

    def list_by_namespace(self, namespace: str) -> list[ToolDescriptor]:
        with self._lock:
            return [t for t in self._tools.values() if t.namespace == namespace]

    def search(self, query: str, *, risk_max: int = 4, limit: int = 15) -> list[ToolDescriptor]:
        """Keyword search over name/description/tags/namespace (lowercase substring).

        Every whitespace-separated keyword must match the combined haystack.
        Tools above ``risk_max`` are never returned unless explicitly... they are
        simply filtered out.
        """
        tokens = [tok for tok in query.lower().split() if tok]
        with self._lock:
            matches: list[ToolDescriptor] = []
            for tool in self._tools.values():
                if tool.risk_level > risk_max:
                    continue
                if tokens:
                    haystack = " ".join(
                        [
                            tool.name,
                            tool.namespace,
                            tool.description,
                            " ".join(tool.tags),
                        ]
                    ).lower()
                    if not all(tok in haystack for tok in tokens):
                        continue
                matches.append(tool)
                if len(matches) >= limit:
                    break
            return matches

    def list_for_llm(
        self,
        *,
        query: str | None = None,
        risk_max: int = 4,
        namespace_filter: Sequence[str] | None = None,
    ) -> list[dict]:
        """Return OpenAI-style ``{"type": "function", ...}`` schemas for LLM injection.

        When ``query`` is given, tools are narrowed via :meth:`search`; otherwise all
        tools within ``risk_max`` are returned.
        """
        with self._lock:
            if query:
                tools = self.search(query, risk_max=risk_max)
            else:
                tools = [t for t in self._tools.values() if t.risk_level <= risk_max]
            if namespace_filter:
                ns = set(namespace_filter)
                tools = [t for t in tools if t.namespace in ns]
            return [tool.to_llm_schema() for tool in tools]
