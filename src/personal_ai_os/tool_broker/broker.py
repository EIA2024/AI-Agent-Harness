"""Tool Broker — authoritative execution boundary for every tool invocation.

Security order matters: capability + schema + policy + approval are checked
before credentials, and any side-effecting call acquires a durable idempotency
claim *before* the connector can run. A replay that finds an ambiguous executing
claim fails closed instead of guessing whether an external side effect happened.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

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

ERR_NOT_FOUND = "TOOL_NOT_FOUND"
ERR_SCHEMA = "SCHEMA_VALIDATION_ERROR"
ERR_DENIED = "POLICY_DENIED"
ERR_CAPABILITY = "CAPABILITY_DENIED"
ERR_CREDENTIALS = "CREDENTIAL_ERROR"
ERR_NO_CONNECTOR = "NO_CONNECTOR"
ERR_TIMEOUT = "TIMEOUT"
ERR_CONNECTOR = "CONNECTOR_ERROR"
ERR_IDEMPOTENCY_REQUIRED = "IDEMPOTENCY_REQUIRED"
ERR_EXECUTION_AMBIGUOUS = "TOOL_EXECUTION_AMBIGUOUS"


def _now() -> datetime:
    return datetime.now(UTC)


class _CredentialExecutionContext(ToolExecutionContext):
    """Ephemeral connector-only context carrying secrets out of normal args."""

    __slots__ = ("secrets",)

    def __init__(self, base: ToolExecutionContext, secrets: dict | None) -> None:
        super().__init__(
            run_id=base.run_id,
            session_id=base.session_id,
            owner_id=base.owner_id,
            agent_id=base.agent_id,
            idempotency_key=base.idempotency_key,
            approved_by=base.approved_by,
            approval_id=base.approval_id,
        )
        self.secrets = dict(secrets or {})


class ToolBroker:
    """Orchestrates lookup, policy, approval, claim, execution and audit."""

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
        self._connectors: dict[str, Any] = dict(connectors or {})
        self._tool_connectors: dict[str, Any] = {}
        self._event_bus = event_bus
        self._max_result_chars = max_result_chars

    # ----------------------------------------------------------- registration

    async def register_connector(self, connector) -> None:
        tools = connector.list_tools()
        if inspect.isawaitable(tools):
            tools = await tools
        for tool in tools:
            existing_executor = self._tool_connectors.get(tool.name)
            if existing_executor is not None and existing_executor is not connector:
                raise ValueError(
                    f"tool name collision for {tool.name!r}: refusing descriptor/executor split"
                )
            self._registry.register(tool)
            self._tool_connectors[tool.name] = connector
            for key in (tool.namespace, tool.name.split(".")[0]):
                existing = self._connectors.get(key)
                if existing is not None and existing is not connector:
                    # Namespace collisions are safe only because exact tool-name
                    # routing wins. Keep the first namespace route for backwards
                    # compatibility but never use it for a known exact tool.
                    logger.warning("connector namespace %r already bound", key)
                else:
                    self._connectors[key] = connector
        self._connectors.setdefault(connector.connector_name, connector)

    def resolve_connector(self, tool: ToolDescriptor):
        exact = self._tool_connectors.get(tool.name)
        if exact is not None:
            return exact
        connector = self._connectors.get(tool.namespace)
        if connector is None:
            connector = self._connectors.get(tool.source)
        if connector is None:
            connector = self._connectors.get(tool.name.split(".")[0])
        return connector

    # --------------------------------------------------------------- execute

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

        # Discovery filtering is UX, not security. Enforce capability again at
        # the authoritative execution boundary so stale/crafted calls cannot bypass it.
        if self._capabilities is not None and not self._capabilities.can_use(
            context.owner_id, tool.name
        ):
            await self._audit("tool.capability_denied", tool, context)
            return await self._finish_failure(
                tool,
                context,
                started,
                err_code=ERR_CAPABILITY,
                message=f"owner is not allowed to execute {tool.name}",
                event=EventTypes.TOOL_DENIED,
                arguments=arguments,
            )

        await self._publish(
            EventTypes.TOOL_REQUESTED,
            context,
            {
                "tool": tool.name,
                "risk_level": tool.risk_level,
                "idempotency_key": context.idempotency_key,
            },
        )

        try:
            validate_arguments(tool.input_schema, arguments)
        except ValueError as exc:
            return await self._finish_failure(
                tool,
                context,
                started,
                err_code=ERR_SCHEMA,
                message=str(exc),
                event=EventTypes.TOOL_FAILED,
                arguments=arguments,
            )

        try:
            decision = await self._policy.evaluate(tool, arguments, context)
        except Exception as exc:  # policy failure must fail closed
            logger.warning("Policy engine error for %s: %s", tool.name, exc)
            return await self._finish_failure(
                tool,
                context,
                started,
                err_code=ERR_DENIED,
                message="Policy engine unavailable",
                event=EventTypes.TOOL_DENIED,
                arguments=arguments,
            )

        if decision.decision == "deny":
            reason = "; ".join(decision.reasons) or "Denied by policy"
            await self._audit("tool.denied", tool, context, {"reason": reason})
            return await self._finish_failure(
                tool,
                context,
                started,
                err_code=ERR_DENIED,
                message=reason,
                event=EventTypes.TOOL_DENIED,
                risk_level=decision.risk_level,
                arguments=arguments,
            )

        if decision.decision == "ask":
            if context.approval_id is not None:
                if self._approval_engine is None:
                    return await self._finish_failure(
                        tool,
                        context,
                        started,
                        err_code=ERR_DENIED,
                        message="Approval engine unavailable; refusing approved execution",
                        event=EventTypes.TOOL_DENIED,
                        risk_level=decision.risk_level,
                        arguments=arguments,
                    )
                verified = await self._approval_engine.verify_approval(
                    context.approval_id, tool.name, arguments
                )
                if not verified:
                    await self._audit(
                        "approval.mismatch_blocked",
                        tool,
                        context,
                        {"approval_id": str(context.approval_id)},
                    )
                    return await self._finish_failure(
                        tool,
                        context,
                        started,
                        err_code=ERR_DENIED,
                        message="Approval is not valid for the exact requested action",
                        event=EventTypes.TOOL_DENIED,
                        risk_level=decision.risk_level,
                        arguments=arguments,
                    )
            else:
                reason = "; ".join(decision.reasons) or "Approval required"
                exc = ApprovalRequiredError(
                    request_id=uuid4(),
                    tool_name=tool.name,
                    risk_level=tool.risk_level,
                    reason=reason,
                )
                # ApprovalRequiredError predates the step-up field and is not a
                # slotted class, so carry the constraint without breaking callers.
                exc.requires_auth_method = decision.constraints.get(
                    "require_auth_method"
                )
                raise exc

        try:
            injected = await self._credentials.inject(
                tool, arguments, context.owner_id
            )
        except Exception as exc:
            return await self._finish_failure(
                tool,
                context,
                started,
                err_code=ERR_CREDENTIALS,
                message=f"Credential injection failed: {exc}",
                event=EventTypes.TOOL_FAILED,
                arguments=arguments,
            )

        connector = self.resolve_connector(tool)
        if connector is None:
            return await self._finish_failure(
                tool,
                context,
                started,
                err_code=ERR_NO_CONNECTOR,
                message=f"No connector registered for tool {tool_name!r}",
                event=EventTypes.TOOL_FAILED,
                arguments=arguments,
            )

        secrets: dict = {}
        exec_args = dict(injected or {})
        raw_secrets = exec_args.pop("_secrets", None)
        if isinstance(raw_secrets, dict):
            secrets = raw_secrets
        exec_context = _CredentialExecutionContext(context, secrets)

        needs_claim = bool(tool.side_effect or tool.external_write or tool.destructive)
        if needs_claim and not context.idempotency_key:
            return await self._finish_failure(
                tool,
                context,
                started,
                err_code=ERR_IDEMPOTENCY_REQUIRED,
                message="Side-effecting tools require a durable idempotency key",
                event=EventTypes.TOOL_DENIED,
                risk_level=decision.risk_level,
                arguments=arguments,
            )

        if needs_claim:
            try:
                claimed = await self._claim_execution(
                    tool, context, arguments, decision.risk_level
                )
            except Exception:  # noqa: BLE001 - side effects fail closed on DB gaps
                logger.warning(
                    "Could not persist execution claim for %s", tool.name, exc_info=True
                )
                return await self._finish_failure(
                    tool,
                    context,
                    started,
                    err_code=ERR_DENIED,
                    message="Blocked: could not persist side-effect execution claim",
                    event=EventTypes.TOOL_DENIED,
                    risk_level=decision.risk_level,
                    arguments=arguments,
                )
            if isinstance(claimed, ToolResult):
                return claimed

        if not await self._durable_intent(tool, context, arguments):
            result = ToolResult.fail(
                error="Blocked: could not persist security intent for high-risk tool",
                error_code=ERR_DENIED,
            )
            return await self._finish_failure(
                tool,
                context,
                started,
                result=result,
                event=EventTypes.TOOL_DENIED,
                risk_level=tool.risk_level,
                arguments=arguments,
            )

        timeout = (
            float(tool.timeout_seconds)
            if tool.timeout_seconds and tool.timeout_seconds > 0
            else 30.0
        )
        try:
            async with asyncio.timeout(timeout):
                result = await connector.execute(tool.name, exec_args, exec_context)
        except TimeoutError:
            result = ToolResult.fail(
                error=f"Tool {tool_name!r} timed out after {timeout}s",
                error_code=ERR_TIMEOUT,
            )
        except Exception as exc:
            logger.warning("Connector %r failed: %s", connector.__class__.__name__, exc)
            result = ToolResult.fail(error=str(exc), error_code=ERR_CONNECTOR)

        result.latency_ms = self._latency(started)
        sanitized = self._sanitize_result(result)
        if not sanitized.success:
            return await self._finish_failure(
                tool,
                context,
                started,
                result=sanitized,
                event=EventTypes.TOOL_FAILED,
                risk_level=decision.risk_level,
                arguments=arguments,
            )

        await self._record(tool, context, arguments, decision, sanitized)
        await self._audit(
            "tool.executed",
            tool,
            context,
            {"success": True, "truncated": sanitized.truncated},
        )
        await self._publish(
            EventTypes.TOOL_COMPLETED,
            context,
            {
                "tool": tool.name,
                "success": True,
                "latency_ms": sanitized.latency_ms,
                "truncated": sanitized.truncated,
            },
        )
        return sanitized

    # ---------------------------------------------------- idempotency claim

    async def _claim_execution(
        self,
        tool: ToolDescriptor,
        context: ToolExecutionContext,
        arguments: dict,
        risk_level: int,
    ) -> ToolResult | None:
        """Persist an execution claim before any connector side effect."""
        from personal_ai_os.db.models import ToolCall
        from personal_ai_os.db.session import session_scope

        idem = context.idempotency_key
        if not idem:
            return ToolResult.fail(
                error="missing idempotency key", error_code=ERR_IDEMPOTENCY_REQUIRED
            )

        # Approved calls reuse the awaiting_approval audit row. The conditional
        # update is itself the execution claim; only one concurrent worker wins.
        if context.approval_id is not None:
            async with session_scope() as session:
                result = await session.execute(
                    update(ToolCall)
                    .where(
                        ToolCall.run_id == context.run_id,
                        ToolCall.approval_id == context.approval_id,
                        ToolCall.status == "awaiting_approval",
                    )
                    .values(
                        idempotency_key=idem,
                        status="executing",
                        arguments=LogSanitizer.sanitize_dict(dict(arguments)),
                        risk_level=risk_level,
                        started_at=_now(),
                    )
                )
                if result.rowcount == 1:
                    return None
            return await self._existing_execution_result(context, idem)

        try:
            async with session_scope() as session:
                session.add(
                    ToolCall(
                        run_id=context.run_id,
                        tool_name=tool.name,
                        arguments=LogSanitizer.sanitize_dict(dict(arguments)),
                        risk_level=risk_level,
                        status="executing",
                        idempotency_key=idem,
                        approval_id=context.approval_id,
                        started_at=_now(),
                    )
                )
                await session.flush()
            return None
        except IntegrityError:
            return await self._existing_execution_result(context, idem)

    async def _existing_execution_result(
        self, context: ToolExecutionContext, idem: str
    ) -> ToolResult:
        from personal_ai_os.db.models import ToolCall
        from personal_ai_os.db.session import session_scope

        async with session_scope() as session:
            row = (
                await session.execute(
                    select(ToolCall).where(
                        ToolCall.run_id == context.run_id,
                        ToolCall.idempotency_key == idem,
                    )
                )
            ).scalar_one_or_none()
            if row is None and context.approval_id is not None:
                row = (
                    await session.execute(
                        select(ToolCall).where(
                            ToolCall.run_id == context.run_id,
                            ToolCall.approval_id == context.approval_id,
                        )
                    )
                ).scalar_one_or_none()
            if row is None:
                return ToolResult.fail(
                    error="execution claim conflict but no durable row was found",
                    error_code=ERR_EXECUTION_AMBIGUOUS,
                )
            if row.status == "success" and isinstance(row.result, dict):
                return _tool_result_from_dict(row.result)
            if row.status in ("error", "denied"):
                error = row.error or {}
                return ToolResult.fail(
                    error=str(error.get("message") or "prior execution failed"),
                    error_code=str(error.get("code") or ERR_CONNECTOR),
                )
            return ToolResult.fail(
                error=(
                    "A prior worker claimed this side effect and its outcome is not "
                    "durably known; refusing to execute it again"
                ),
                error_code=ERR_EXECUTION_AMBIGUOUS,
            )

    # -------------------------------------------------------------- discovery

    async def list_available_tools(
        self, *, owner_id: UUID, query: str | None = None
    ) -> list[ToolDescriptor]:
        if self._capabilities is not None:
            return self._capabilities.visible_tools(owner_id, query=query)
        if query:
            return self._registry.search(query, risk_max=4)
        return [t for t in self._registry.list_all() if t.risk_level <= 4]

    async def validate_tool(
        self, tool_name: str, arguments: dict, owner_id: UUID
    ) -> ToolResult:
        tool = self._registry.get(tool_name)
        if tool is None:
            return ToolResult.fail(
                error=f"Tool {tool_name!r} not found in registry",
                error_code=ERR_NOT_FOUND,
            )
        if self._capabilities is not None and not self._capabilities.can_use(
            owner_id, tool_name
        ):
            return ToolResult.fail(
                error=f"owner is not allowed to use {tool_name}",
                error_code=ERR_CAPABILITY,
            )
        try:
            validate_arguments(tool.input_schema, arguments)
        except ValueError as exc:
            return ToolResult.fail(error=str(exc), error_code=ERR_SCHEMA)
        return ToolResult.ok(
            data={"valid": True, "tool": tool_name},
            text=f"{tool_name}: arguments valid (dry-run, not executed)",
        )

    # --------------------------------------------------------------- internals

    def _sanitize_result(self, result: ToolResult) -> ToolResult:
        if result.data is not None:
            result.data = LogSanitizer.sanitize_dict(result.data)
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
            tool,
            context,
            arguments or {},
            None,
            result,
            risk_level=risk_level if risk_level is not None else tool.risk_level,
        )
        await self._publish(
            event,
            context,
            {
                "tool": tool.name,
                "success": False,
                "error_code": result.error_code,
                "latency_ms": result.latency_ms,
            },
        )
        return result

    async def _publish(
        self, event_type: str, context: ToolExecutionContext, payload: dict
    ) -> None:
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
        except Exception as exc:  # telemetry is fail-soft
            logger.warning("Failed to publish %s event: %s", event_type, exc)

    async def _durable_intent(
        self,
        tool: ToolDescriptor,
        context: ToolExecutionContext,
        arguments: dict,
    ) -> bool:
        if (tool.risk_level or 0) < 3:
            return True
        try:
            from personal_ai_os.db.models import AuditEvent
            from personal_ai_os.db.session import session_scope

            safe_arguments = LogSanitizer.sanitize_dict(dict(arguments))
            async with session_scope() as session:
                session.add(
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
                            "arguments": safe_arguments,
                            "intent": True,
                        },
                    )
                )
            return True
        except Exception as exc:  # high-risk audit is fail-closed
            logger.error(
                "Durable audit intent FAILED for R%d tool %s: %s",
                tool.risk_level,
                tool.name,
                exc,
            )
            return False

    async def _audit(
        self,
        event_type: str,
        tool: ToolDescriptor,
        context: ToolExecutionContext,
        details: dict | None = None,
    ) -> None:
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
        except Exception as exc:
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
        """Persist/update ToolCall. A pre-execution claim is updated in-place."""
        try:
            from personal_ai_os.db.models import ToolCall
            from personal_ai_os.db.session import session_scope

            if result.success:
                status = "success"
                result_col = result.to_dict()
                error_col = None
            elif result.error_code in (ERR_DENIED, ERR_CAPABILITY):
                status = "denied"
                result_col = None
                error_col = {"code": result.error_code, "message": result.error}
            else:
                status = "error"
                result_col = None
                error_col = {"code": result.error_code, "message": result.error}

            safe_args = LogSanitizer.sanitize_dict(dict(arguments))
            async with session_scope() as session:
                existing = None
                if context.idempotency_key:
                    existing = (
                        await session.execute(
                            select(ToolCall).where(
                                ToolCall.run_id == context.run_id,
                                ToolCall.idempotency_key == context.idempotency_key,
                            )
                        )
                    ).scalar_one_or_none()
                if existing is None and context.approval_id is not None:
                    existing = (
                        await session.execute(
                            select(ToolCall).where(
                                ToolCall.run_id == context.run_id,
                                ToolCall.approval_id == context.approval_id,
                            )
                        )
                    ).scalar_one_or_none()
                if existing is not None:
                    existing.status = status
                    existing.result = result_col
                    existing.error = error_col
                    existing.arguments = safe_args
                    existing.idempotency_key = (
                        context.idempotency_key or existing.idempotency_key
                    )
                    existing.completed_at = _now()
                    await session.flush()
                    return

                session.add(
                    ToolCall(
                        run_id=context.run_id,
                        tool_name=tool.name,
                        arguments=safe_args,
                        risk_level=(
                            risk_level if risk_level is not None else tool.risk_level
                        ),
                        status=status,
                        result=result_col,
                        error=error_col,
                        idempotency_key=context.idempotency_key or None,
                        approval_id=context.approval_id,
                        completed_at=_now(),
                    )
                )
        except Exception as exc:  # recording low-risk failures remains fail-soft
            logger.warning("Failed to record tool call %s: %s", tool.name, exc)

    @staticmethod
    def _latency(started: float) -> int:
        return int(round((time.perf_counter() - started) * 1000))


def _tool_result_from_dict(data: dict) -> ToolResult:
    """Rehydrate a durable successful result without re-running a connector."""
    return ToolResult(
        success=bool(data.get("success", True)),
        data=data.get("data"),
        text=data.get("text"),
        error=data.get("error"),
        error_code=data.get("error_code"),
        latency_ms=int(data.get("latency_ms", 0) or 0),
        truncated=bool(data.get("truncated", False)),
        raw_size_bytes=int(data.get("raw_size_bytes", 0) or 0),
    )
