"""Tool Broker — the single entry point for every tool invocation.

Pipeline (blueprint §15): lookup → JSON Schema validation → policy
(allow/ask/deny) → credential injection → connector execution (with timeout)
→ result sanitization/truncation → event publishing → ToolCall audit record.

PolicyEngine and CredentialBroker are injected via the constructor; the broker
never imports their concrete implementations.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any
from uuid import UUID, uuid4

from personal_ai_os.common.models import (
    ApprovalRequiredError,
    DomainEvent,
    EventTypes,
    ToolDescriptor,
    ToolExecutionContext,
    ToolResult,
)
from personal_ai_os.common.utils import LogSanitizer
from personal_ai_os.tool_broker.registry import ToolRegistry
from personal_ai_os.tool_broker.schema import validate_arguments

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stable error codes returned by the broker (not connector-specific)
# ---------------------------------------------------------------------------

ERR_NOT_FOUND = "TOOL_NOT_FOUND"
ERR_SCHEMA = "SCHEMA_VALIDATION_ERROR"
ERR_DENIED = "POLICY_DENIED"
ERR_CREDENTIALS = "CREDENTIAL_ERROR"
ERR_NO_CONNECTOR = "NO_CONNECTOR"
ERR_TIMEOUT = "TIMEOUT"
ERR_CONNECTOR = "CONNECTOR_ERROR"


class ToolBroker:
    """Orchestrates the full tool execution pipeline."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy_engine,
        credential_broker,
        connectors: dict | None = None,
        event_bus=None,
        approval_engine=None,
        audit_logger=None,
        max_result_chars: int = 8000,
        capabilities=None,
    ) -> None:
        self._registry = registry
        self._policy = policy_engine
        self._credentials = credential_broker
        self._approval_engine = approval_engine
        self._audit_logger = audit_logger
        self._capabilities = capabilities
        # keyed by namespace / source / name prefix → Connector
        self._connectors: dict[str, Any] = dict(connectors or {})
        self._event_bus = event_bus
        self._max_result_chars = max_result_chars

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register_connector(self, connector) -> None:
        """Register a connector's tools into the registry and index the connector
        for lookup.

        The Connector Protocol declares ``list_tools()`` as async, so this method
        is async and must be awaited.
        """
        tools = connector.list_tools()
        if inspect.isawaitable(tools):
            tools = await tools
        for tool in tools:
            self._registry.register(tool)
            self._connectors.setdefault(tool.namespace, connector)
            self._connectors.setdefault(tool.name.split(".")[0], connector)
        self._connectors.setdefault(connector.connector_name, connector)

    # ------------------------------------------------------------------
    # Connector resolution
    # ------------------------------------------------------------------

    def resolve_connector(self, tool: ToolDescriptor):
        """Resolve the connector responsible for ``tool``.

        Lookup order:
        1. ``connectors[tool.namespace]``
        2. ``connectors[tool.source]``
        3. ``connectors[tool.name.split('.')[0]]``
        """
        connector = self._connectors.get(tool.namespace)
        if connector is None:
            connector = self._connectors.get(tool.source)
        if connector is None:
            connector = self._connectors.get(tool.name.split(".")[0])
        return connector

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        context: ToolExecutionContext,
    ) -> ToolResult:
        started = time.perf_counter()
        tool = self._registry.get(tool_name)
        if tool is None:
            return ToolResult.fail(
                error=f"Tool {tool_name!r} not found in registry",
                error_code=ERR_NOT_FOUND,
            )

        await self._publish(
            EventTypes.TOOL_REQUESTED,
            context,
            {"tool": tool.name, "risk_level": tool.risk_level, "idempotency_key": context.idempotency_key},
        )

        # 1. JSON Schema validation -------------------------------------
        try:
            validate_arguments(tool.input_schema, arguments)
        except ValueError as exc:
            return await self._finish_failure(
                tool, context, started, err_code=ERR_SCHEMA, message=str(exc),
                event=EventTypes.TOOL_FAILED, arguments=arguments,
            )

        # 2. Policy check -------------------------------------------------
        try:
            decision = await self._policy.evaluate(tool, arguments, context)
        except Exception as exc:  # policy engine failure → fail closed
            logger.warning("Policy engine error for %s: %s", tool.name, exc)
            return await self._finish_failure(
                tool, context, started, err_code=ERR_DENIED,
                message="Policy engine unavailable", event=EventTypes.TOOL_DENIED,
                arguments=arguments,
            )

        if decision.decision == "deny":
            reason = "; ".join(decision.reasons) or "Denied by policy"
            await self._audit("tool.denied", tool, context, {"reason": reason})
            return await self._finish_failure(
                tool, context, started, err_code=ERR_DENIED, message=reason,
                event=EventTypes.TOOL_DENIED, risk_level=decision.risk_level,
                arguments=arguments,
            )
        if decision.decision == "ask":
            # An approved execution carries an approval id in the context.
            # Re-verification at the broker (the security boundary) is what
            # prevents "approved A, executing B": the exact arguments must match
            # the hash bound when the approval was resolved.
            if context.approval_id is not None and self._approval_engine is not None:
                verified = await self._approval_engine.verify_approval(
                    context.approval_id, tool.name, arguments
                )
                if not verified:
                    await self._audit(
                        "approval.mismatch_blocked", tool, context,
                        {"approval_id": str(context.approval_id)},
                    )
                    return await self._finish_failure(
                        tool, context, started, err_code=ERR_DENIED,
                        message=(
                            "Approval does not match the requested arguments — "
                            "executing B under approval for A is blocked"
                        ),
                        event=EventTypes.TOOL_DENIED, risk_level=decision.risk_level,
                        arguments=arguments,
                    )
                # verified → proceed to execute below (bypass the ask)
            else:
                reason = "; ".join(decision.reasons) or "Approval required"
                raise ApprovalRequiredError(
                    request_id=uuid4(),
                    tool_name=tool.name,
                    risk_level=tool.risk_level,
                    reason=reason,
                )

        # 3. Credential injection -----------------------------------------
        try:
            injected = await self._credentials.inject(tool, arguments, context.owner_id)
        except Exception as exc:
            return await self._finish_failure(
                tool, context, started, err_code=ERR_CREDENTIALS,
                message=f"Credential injection failed: {exc}", event=EventTypes.TOOL_FAILED,
                arguments=arguments,
            )

        # 4. Execute ------------------------------------------------------
        connector = self.resolve_connector(tool)
        if connector is None:
            return await self._finish_failure(
                tool, context, started, err_code=ERR_NO_CONNECTOR,
                message=f"No connector registered for tool {tool_name!r}",
                event=EventTypes.TOOL_FAILED, arguments=arguments,
            )

        # Secrets (injected under `_secrets`) must never reach the connector as
        # regular arguments: strip the envelope before execution. A connector
        # that legitimately needs credentials should consume them via a future
        # dedicated channel on the execution context, never via arbitrary args.
        exec_args = injected
        if isinstance(exec_args, dict) and "_secrets" in exec_args:
            exec_args = {k: v for k, v in exec_args.items() if k != "_secrets"}

        timeout = float(tool.timeout_seconds) if tool.timeout_seconds and tool.timeout_seconds > 0 else 30.0
        # P1-033: for R3/R4 tools a durable security intent must be persisted
        # BEFORE execution; if it cannot be, fail closed rather than run an
        # untracked side effect.
        if not await self._durable_intent(tool, context, arguments):
            await self._audit(
                "tool.intent_blocked", tool, context,
                {"reason": "durable audit intent could not be persisted"},
            )
            return await self._finish_failure(
                tool, context, started, err_code=ERR_DENIED,
                message="Blocked: could not persist the security intent for this R3/R4 tool",
                event=EventTypes.TOOL_DENIED, risk_level=tool.risk_level, arguments=arguments,
            )
        try:
            async with asyncio.timeout(timeout):
                result = await connector.execute(tool.name, exec_args, context)
        except TimeoutError:
            result = ToolResult.fail(
                error=f"Tool {tool_name!r} timed out after {timeout}s", error_code=ERR_TIMEOUT,
            )
        except Exception as exc:
            logger.warning("Connector %r failed: %s", connector.__class__.__name__, exc)
            result = ToolResult.fail(error=str(exc), error_code=ERR_CONNECTOR)

        latency = self._latency(started)
        result.latency_ms = latency
        sanitized = self._sanitize_result(result)

        if not sanitized.success:
            return await self._finish_failure(
                tool, context, started, result=sanitized, event=EventTypes.TOOL_FAILED,
                risk_level=decision.risk_level, arguments=injected,
            )

        await self._record(tool, context, injected, decision, sanitized)
        await self._audit("tool.executed", tool, context, {"success": True, "truncated": sanitized.truncated})
        await self._publish(
            EventTypes.TOOL_COMPLETED,
            context,
            {"tool": tool.name, "success": True, "latency_ms": latency, "truncated": sanitized.truncated},
        )
        return sanitized

    async def list_available_tools(
        self,
        *,
        owner_id: UUID,
        query: str | None = None,
    ) -> list[ToolDescriptor]:
        """Return tools visible to ``owner_id`` (P1-014).

        When a :class:`~personal_ai_os.gateway.capabilities.CapabilityService`
        is injected it owns owner filtering; otherwise the legacy risk-only
        filter applies.
        """
        if self._capabilities is not None:
            return self._capabilities.visible_tools(owner_id, query=query)
        if query:
            return self._registry.search(query, risk_max=4)
        return [t for t in self._registry.list_all() if t.risk_level <= 4]

    async def validate_tool(
        self,
        tool_name: str,
        arguments: dict,
        owner_id: UUID,
    ) -> ToolResult:
        """Validate a tool call WITHOUT executing it (P1-015).

        Replaces the former ``test_tool`` which executed the connector while
        bypassing policy, approval and audit. This only checks that the tool is
        registered and the arguments match its schema — a dry-run simulation
        that can never touch a side effect.
        """
        tool = self._registry.get(tool_name)
        if tool is None:
            return ToolResult.fail(
                error=f"Tool {tool_name!r} not found in registry", error_code=ERR_NOT_FOUND,
            )
        try:
            validate_arguments(tool.input_schema, arguments)
        except ValueError as exc:
            return ToolResult.fail(error=str(exc), error_code=ERR_SCHEMA)
        return ToolResult.ok(
            data={"valid": True, "tool": tool_name},
            text=f"{tool_name}: arguments valid (dry-run, not executed)",
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sanitize_result(self, result: ToolResult) -> ToolResult:
        """Redact secrets from the result and truncate oversized text/data."""
        if result.data is not None:
            result.data = LogSanitizer.sanitize_dict(result.data)
            # P1-001: bound the structured data a connector can stuff into the
            # result (a huge dict is a memory/context risk).
            import json as _json

            try:
                encoded = _json.dumps(result.data, ensure_ascii=False)
            except (TypeError, ValueError):
                encoded = ""
            if len(encoded) > self._max_result_chars:
                result.data = {"truncated": True, "bytes": len(encoded)}
                result.raw_size_bytes = len(encoded)
        if result.text:
            raw_len = len(result.text)
            result.text = LogSanitizer.sanitize(result.text)
            if len(result.text) > self._max_result_chars:
                result.raw_size_bytes = raw_len
                result.text = result.text[: self._max_result_chars]
                result.truncated = True
        return result

    async def _finish_failure(
        self,
        tool: ToolDescriptor,
        context: ToolExecutionContext,
        started: float,
        *,
        result: ToolResult | None = None,
        err_code: str | None = None,
        message: str | None = None,
        event: str,
        risk_level: int | None = None,
        arguments: dict | None = None,
    ) -> ToolResult:
        if result is None:
            result = ToolResult.fail(
                error=message or "Tool failed",
                error_code=err_code or ERR_CONNECTOR,
                latency_ms=self._latency(started),
            )
        result.latency_ms = self._latency(started)
        await self._record(
            tool, context, arguments or {}, None, result, risk_level=risk_level or tool.risk_level,
        )
        await self._publish(
            event, context,
            {"tool": tool.name, "success": False, "error_code": result.error_code, "latency_ms": result.latency_ms},
        )
        return result

    async def _publish(self, event_type: str, context: ToolExecutionContext, payload: dict) -> None:
        if self._event_bus is None:
            return
        try:
            event = DomainEvent(
                type=event_type,
                owner_id=context.owner_id,
                run_id=context.run_id,
                session_id=context.session_id,
                payload=payload,
            )
            await self._event_bus.publish(event)
        except Exception as exc:  # event publishing must never break execution
            logger.warning("Failed to publish %s event: %s", event_type, exc)

    async def _durable_intent(
        self,
        tool: ToolDescriptor,
        context: ToolExecutionContext,
        arguments: dict,
    ) -> bool:
        """Persist a durable security intent before an R3/R4 tool executes (P1-033).

        A best-effort audit is not enough for high-risk tools: the intent must
        survive a crash so a later reconciler can prove the tool ran. Returns
        False when the intent could not be persisted (caller fails closed).
        """
        if (tool.risk_level or 0) < 3:
            return True
        try:
            from personal_ai_os.db.models import AuditEvent
            from personal_ai_os.db.session import session_scope

            async with session_scope() as s:
                s.add(
                    AuditEvent(
                        owner_id=context.owner_id,
                        actor_type="agent",
                        actor_id=str(context.run_id),
                        event_type="tool.intent",
                        resource_type="tool",
                        resource_id=tool.name,
                        details={
                            "tool": tool.name,
                            "risk_level": tool.risk_level,
                            "run_id": str(context.run_id),
                            "arguments": arguments,
                            "intent": True,
                        },
                    )
                )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Durable audit intent FAILED for R%d tool %s: %s — failing closed",
                tool.risk_level, tool.name, exc,
            )
            return False

    async def _audit(
        self,
        event_type: str,
        tool: ToolDescriptor,
        context: ToolExecutionContext,
        details: dict | None = None,
    ) -> None:
        """Write a security-relevant audit record. Fail-soft: never breaks execution."""
        if self._audit_logger is None:
            return
        try:
            await self._audit_logger.log(
                owner_id=context.owner_id,
                actor_type="agent",
                actor_id=str(context.run_id),
                event_type=event_type,
                resource_type="tool",
                resource_id=tool.name,
                details={
                    "tool": tool.name,
                    "risk_level": tool.risk_level,
                    "run_id": str(context.run_id),
                    **(details or {}),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Audit log failed for %s: %s", tool.name, exc)

    async def _record(
        self,
        tool: ToolDescriptor,
        context: ToolExecutionContext,
        arguments: dict,
        decision,
        result: ToolResult,
        *,
        risk_level: int | None = None,
    ) -> None:
        """Persist a ToolCall audit row. Fail-soft: a recording error must never
        surface to the caller of execute()."""
        try:
            from personal_ai_os.db.models import ToolCall
            from personal_ai_os.db.session import session_scope

            if result.success:
                status = "success"
                result_col = result.to_dict()
                error_col = None
            elif result.error_code == ERR_DENIED:
                status = "denied"
                result_col = None
                error_col = {"code": result.error_code, "message": result.error}
            else:
                status = "error"
                result_col = None
                error_col = {"code": result.error_code, "message": result.error}

            async with session_scope() as session:
                # An approved execution carries an approval id: the awaiting_approval
                # row created by the runtime must be updated, not duplicated.
                if context.approval_id is not None:
                    from sqlalchemy import select as _select

                    pending = (
                        await session.execute(
                            _select(ToolCall).where(
                                ToolCall.run_id == context.run_id,
                                ToolCall.approval_id == context.approval_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if pending is not None:
                        pending.status = status
                        pending.result = result_col
                        pending.error = error_col
                        pending.arguments = LogSanitizer.sanitize_dict(dict(arguments))
                        pending.completed_at = None
                        await session.flush()
                        return

                row = ToolCall(
                    run_id=context.run_id,
                    tool_name=tool.name,
                    arguments=LogSanitizer.sanitize_dict(dict(arguments)),
                    risk_level=risk_level if risk_level is not None else tool.risk_level,
                    status=status,
                    result=result_col,
                    error=error_col,
                    idempotency_key=context.idempotency_key or None,
                    approval_id=context.approval_id,
                )
                session.add(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to record tool call %s: %s", tool.name, exc)

    @staticmethod
    def _latency(started: float) -> int:
        return int(round((time.perf_counter() - started) * 1000))
