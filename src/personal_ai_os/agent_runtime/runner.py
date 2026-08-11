"""Run lifecycle manager for the Agent Runtime (§9 Run API, §32 durable execution).

:class:`RunRunner` owns persistence for a single Run:

- creates the ``runs`` row and the user ``messages`` row on :meth:`start`,
- streams the LangGraph state machine, persisting ``Run.state`` (JSONB) and one
  ``RunStep`` per node,
- persists ``Message`` / ``ToolCall`` rows derived from the evolving state,
- publishes lifecycle events (``run.started`` / ``run.completed`` /
  ``run.failed`` / ``run.cancelled`` / ``approval.*``),
- supports approval resume (:meth:`resume`) and :meth:`cancel`.

Persistence is centralized here (rather than inside the graph nodes) so the
graph stays a pure state transition machine and can be unit-tested without a
database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy import update as sa_update

from personal_ai_os.common.models import (
    AgentOSError,
    DomainEvent,
    ErrorCode,
    EventTypes,
)
from personal_ai_os.db.models import Approval, Message, Run, RunStep, ToolCall
from personal_ai_os.db.session import session_scope

_MAX_TOOL_RESULTS_PERSISTED = 50


def _now() -> datetime:
    return datetime.now(UTC)


def _coerce_uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


class RunRunner:
    """Executes and persists agent runs. All services are injected."""

    def __init__(
        self,
        *,
        graph: Any,
        model_provider: Any,
        tool_broker: Any,
        memory_store: Any | None = None,
        context_engine: Any,
        policy_engine: Any,
        approval_engine: Any | None = None,
        event_bus: Any | None = None,
        classifier: Any | None = None,
        planner: Any | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self.graph = graph
        self.model_provider = model_provider
        self.tool_broker = tool_broker
        self.memory_store = memory_store
        self.context_engine = context_engine
        self.policy_engine = policy_engine  # policy decisions are handled by the tool broker
        self.approval_engine = approval_engine
        self.event_bus = event_bus
        self.classifier = classifier
        self.planner = planner

        if hasattr(graph, "astream"):
            self.compiled = graph
        else:
            self.compiled = graph.compile(checkpointer=checkpointer or InMemorySaver())

        # in-memory bookkeeping for persistence dedup (keyed by run id)
        self._persisted_tool_ids: dict[str, set[str]] = {}
        self._persisted_message_count: dict[str, int] = {}

    # ------------------------------------------------------------------ start

    async def start(
        self,
        *,
        session_id,
        owner_id,
        user_input: str,
        agent_id=None,
    ) -> dict:
        """Create a Run and execute it to completion (or approval pause)."""
        run_id = uuid.uuid4()
        owner_uuid = _coerce_uuid(owner_id)
        session_uuid = _coerce_uuid(session_id)
        agent_uuid = _coerce_uuid(agent_id) if agent_id else None
        started = _now()

        async with session_scope() as session:
            session.add(
                Run(
                    id=run_id,
                    owner_id=owner_uuid,
                    session_id=session_uuid,
                    agent_id=agent_uuid,
                    status="running",
                    input={"user_input": user_input, "agent_id": str(agent_uuid) if agent_uuid else None},
                    state={},
                    model_usage={},
                    cost={},
                    started_at=started,
                )
            )
            # Flush first: the sessions<->runs FK cycle makes SQLAlchemy otherwise
            # emit the messages insert before the runs insert, violating the FK.
            await session.flush()
            session.add(
                Message(
                    session_id=session_uuid,
                    run_id=run_id,
                    owner_id=owner_uuid,
                    role="user",
                    content=user_input,
                    metadata_={"channel": "api"},
                )
            )

        if self.event_bus is not None:
            await self.event_bus.publish(
                DomainEvent(
                    type=EventTypes.RUN_STARTED,
                    owner_id=owner_uuid,
                    run_id=run_id,
                    session_id=session_uuid,
                    payload={"user_input": user_input},
                )
            )

        initial: dict = {
            "run_id": str(run_id),
            "session_id": str(session_uuid),
            "owner_id": str(owner_uuid),
            "agent_id": str(agent_uuid) if agent_uuid else None,
            "user_input": user_input,
            "messages": [],
            "context_items": [],
            "task": {},
            "plan": [],
            "current_step": 0,
            "pending_tool_call": None,
            "tool_results": [],
            "pending_approval": None,
            "memory_candidates": [],
            "skill_candidates": [],
            "status": "intake",
            "error": {},
            "model_usage": {},
        }
        self._persisted_tool_ids[str(run_id)] = set()
        self._persisted_message_count[str(run_id)] = 1  # the user message above

        config = {"configurable": {"thread_id": str(run_id)}}
        try:
            interrupt_payload = await self._stream(run_id, initial, config)
            if interrupt_payload is not None:
                await self._handle_approval_interrupt(run_id, interrupt_payload)
            else:
                await self._finalize(run_id, success=True)
            return await self.get_run(run_id)
        except Exception as exc:
            await self._fail(run_id, exc)
            raise

    # ------------------------------------------------------------------ resume

    async def resume(
        self,
        run_id,
        *,
        approval_id=None,
        decision: str | None = None,
        edited_arguments: dict | None = None,
    ) -> dict:
        """Resume a paused run (approval granted/rejected via HITL)."""
        run_uuid = _coerce_uuid(run_id)
        run_row = await self._get_run_row(run_uuid)
        if run_row is None:
            raise KeyError(f"Run not found: {run_id}")
        state = dict(run_row.state or {})
        if state.get("status") != "waiting_approval":
            raise ValueError("Run is not waiting for approval")

        decision = (decision or "approved").lower()
        approved_by = run_row.owner_id

        if self.approval_engine is not None and approval_id is not None:
            await self.approval_engine.resolve(
                approval_id, decision=decision, approved_by=approved_by, edited_arguments=edited_arguments
            )
        elif approval_id is not None:
            async with session_scope() as session:
                approval = await session.get(Approval, _coerce_uuid(approval_id))
                if approval is not None:
                    approval.status = "approved" if decision == "approved" else "rejected"
                    approval.approved_by = approved_by

        resume_value: dict = {"decision": decision, "approved_by": str(approved_by)}
        if edited_arguments:
            resume_value["edited_arguments"] = edited_arguments

        if self.event_bus is not None and approval_id is not None:
            event_type = (
                EventTypes.APPROVAL_APPROVED if decision == "approved" else EventTypes.APPROVAL_REJECTED
            )
            await self.event_bus.publish(
                DomainEvent(
                    type=event_type,
                    owner_id=run_row.owner_id,
                    run_id=run_uuid,
                    session_id=run_row.session_id,
                    payload={"approval_id": str(approval_id), "decision": decision},
                )
            )

        config = {"configurable": {"thread_id": str(run_uuid)}}
        try:
            interrupt_payload = await self._stream(run_uuid, state, config, resume_value=resume_value)
            if interrupt_payload is not None:
                await self._handle_approval_interrupt(run_uuid, interrupt_payload)
            else:
                await self._finalize(run_uuid, success=True)
            return await self.get_run(run_uuid)
        except Exception as exc:
            await self._fail(run_uuid, exc)
            raise

    # ------------------------------------------------------------------ misc

    async def cancel(self, run_id) -> None:
        """Cancel a run and reject any of its pending approvals."""
        run_uuid = _coerce_uuid(run_id)
        async with session_scope() as session:
            run = await session.get(Run, run_uuid)
            if run is None:
                raise KeyError(f"Run not found: {run_id}")
            run.status = "cancelled"
            run.completed_at = _now()
            state = dict(run.state or {})
            state["status"] = "cancelled"
            run.state = state
            await session.execute(
                sa_update(Approval)
                .where(Approval.run_id == run_uuid, Approval.status == "pending")
                .values(status="rejected")
            )
            owner_uuid = run.owner_id
            session_id = run.session_id
        if self.event_bus is not None:
            await self.event_bus.publish(
                DomainEvent(
                    type=EventTypes.RUN_CANCELLED,
                    owner_id=owner_uuid,
                    run_id=run_uuid,
                    session_id=session_id,
                    payload={"reason": "cancelled_by_user"},
                )
            )

    async def get_run(self, run_id) -> dict:
        """Read a Run record from the DB as a plain dict."""
        run_uuid = _coerce_uuid(run_id)
        async with session_scope() as session:
            run = await session.get(Run, run_uuid)
            if run is None:
                raise KeyError(f"Run not found: {run_id}")
            return {
                "id": str(run.id),
                "run_id": str(run.id),  # convenience alias for the API contract
                "owner_id": str(run.owner_id),
                "session_id": str(run.session_id) if run.session_id else None,
                "agent_id": str(run.agent_id) if run.agent_id else None,
                "status": run.status,
                "input": run.input,
                "state": run.state,
                "model_usage": run.model_usage,
                "cost": run.cost,
                "error": run.error,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            }

    # ------------------------------------------------------------ internals

    async def _stream(
        self,
        run_id: uuid.UUID,
        initial: dict,
        config: dict,
        resume_value: dict | None = None,
    ) -> dict | None:
        """Run the graph, persisting state/step/messages/tools per node.

        Returns the interrupt payload if the graph paused for approval,
        otherwise ``None``.
        """
        interrupt_payload: dict | None = None
        running = dict(initial)
        inputs: Any = Command(resume=resume_value) if resume_value is not None else initial

        try:
            async for chunk in self.compiled.astream(inputs, config=config, stream_mode="updates"):
                if "__interrupt__" in chunk:
                    interrupts = chunk["__interrupt__"]
                    if interrupts:
                        first = interrupts[0]
                        interrupt_payload = getattr(first, "value", first)
                    await self._persist_step(run_id, "approval", running, status="waiting_approval")
                    break
                for node_name, update in chunk.items():
                    running.update(update)
                    await self._persist_step(run_id, node_name, running, update=update)
        except Exception:
            # let the caller classify/persist the failure
            running["status"] = "failed"
            raise

        return interrupt_payload

    async def _persist_step(
        self,
        run_id: uuid.UUID,
        node_name: str,
        running: dict,
        *,
        status: str | None = None,
        update: dict | None = None,
    ) -> None:
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is None:
                return
            run.state = running
            if running.get("model_usage"):
                run.model_usage = running["model_usage"]
            run.status = status or running.get("status") or run.status
            session.add(
                RunStep(
                    run_id=run_id,
                    step_type=node_name,
                    status=run.status,
                    data=dict(update or {}),
                )
            )
            await self._persist_messages(session, run_id, running)
            await self._persist_tool_calls(session, run_id, running)

    async def _persist_messages(self, session, run_id: uuid.UUID, running: dict) -> None:
        messages = running.get("messages") or []
        last = self._persisted_message_count.get(str(run_id), 0)
        run_row = await session.get(Run, run_id)
        if run_row is None:
            return
        for message in messages[last:]:
            content = message.get("content")
            session.add(
                Message(
                    session_id=run_row.session_id,
                    run_id=run_id,
                    owner_id=run_row.owner_id,
                    role=message.get("role") or "assistant",
                    content=content or "",
                    tool_name=message.get("name"),
                    metadata_={"tool_call_id": message.get("tool_call_id")} if message.get("tool_call_id") else {},
                )
            )
        self._persisted_message_count[str(run_id)] = len(messages)

    async def _persist_tool_calls(self, session, run_id: uuid.UUID, running: dict) -> None:
        seen = self._persisted_tool_ids.setdefault(str(run_id), set())
        for entry in (running.get("tool_results") or [])[-_MAX_TOOL_RESULTS_PERSISTED:]:
            entry_id = entry.get("id")
            if not entry_id or entry_id in seen:
                continue
            seen.add(entry_id)
            success = bool(entry.get("success", True))
            approval_uuid = _coerce_uuid(entry["approval_id"]) if entry.get("approval_id") else None
            idem_key = entry.get("idempotency_key")

            # The ToolBroker already records one ToolCall row per execution.
            # Find that row (by approval link for HITL flows) and update it.
            # A row the broker already wrote (found by idempotency key) is
            # left untouched — the broker's status is authoritative there.
            existing: ToolCall | None = None
            if approval_uuid is not None:
                existing = (
                    await session.execute(
                        select(ToolCall).where(
                            ToolCall.run_id == run_id,
                            ToolCall.approval_id == approval_uuid,
                        )
                    )
                ).scalar_one_or_none()
            if existing is not None:
                existing.status = "completed" if success else "failed"
                existing.result = entry if success else None
                existing.error = {"code": entry.get("error_code"), "message": entry.get("error")} if not success else None
                existing.completed_at = _now()
                continue
            if idem_key:
                broker_row = (
                    await session.execute(
                        select(ToolCall).where(
                            ToolCall.run_id == run_id,
                            ToolCall.idempotency_key == idem_key,
                        )
                    )
                ).scalar_one_or_none()
                if broker_row is not None:
                    # Already recorded by the broker with full status semantics.
                    continue

            session.add(
                ToolCall(
                    run_id=run_id,
                    tool_name=entry.get("tool_name", ""),
                    arguments=entry.get("arguments") or {},
                    risk_level=int(entry.get("risk_level", 0) or 0),
                    approval_id=approval_uuid,
                    status="completed" if success else "failed",
                    result=entry if success else None,
                    error={"code": entry.get("error_code"), "message": entry.get("error")} if not success else None,
                    idempotency_key=idem_key,
                )
            )

    async def _handle_approval_interrupt(self, run_id: uuid.UUID, payload: dict) -> None:
        approval_uuid = _coerce_uuid(payload.get("approval_id"))
        tool_name = payload.get("tool_name", "")
        risk_level = int(payload.get("risk_level", 0) or 0)
        reason = payload.get("reason", "")
        if approval_uuid is None:
            return

        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is None:
                return
            state = dict(run.state or {})
            pending_tool_call = state.get("pending_tool_call") or {}
            function = pending_tool_call.get("function") if isinstance(pending_tool_call, dict) else None
            arguments = {}
            if isinstance(function, dict):
                import json

                raw_args = function.get("arguments")
                if isinstance(raw_args, dict):
                    arguments = raw_args
                elif isinstance(raw_args, str):
                    try:
                        arguments = json.loads(raw_args or "{}")
                    except Exception:
                        arguments = {}

            existing = await session.get(Approval, approval_uuid)
            if existing is None:
                session.add(
                    Approval(
                        id=approval_uuid,
                        run_id=run_id,
                        session_id=run.session_id,
                        owner_id=run.owner_id,
                        action_summary=f"执行工具 {tool_name}",
                        tool_name=tool_name,
                        arguments_preview=arguments,
                        risk_level=risk_level,
                        risk_reason=reason,
                        status="pending",
                    )
                )
                session.add(
                    ToolCall(
                        run_id=run_id,
                        tool_name=tool_name,
                        arguments=arguments,
                        risk_level=risk_level,
                        approval_id=approval_uuid,
                        status="awaiting_approval",
                    )
                )
            state["status"] = "waiting_approval"
            state["pending_approval"] = {
                "approval_id": str(approval_uuid),
                "tool_name": tool_name,
                "risk_level": risk_level,
                "reason": reason,
            }
            run.state = state
            run.status = "waiting_approval"
            owner_uuid = run.owner_id
            session_id = run.session_id

        if self.event_bus is not None:
            await self.event_bus.publish(
                DomainEvent(
                    type=EventTypes.APPROVAL_CREATED,
                    owner_id=owner_uuid,
                    run_id=run_id,
                    session_id=session_id,
                    payload={
                        "approval_id": str(approval_uuid),
                        "tool_name": tool_name,
                        "risk_level": risk_level,
                        "reason": reason,
                    },
                )
            )

    async def _finalize(self, run_id: uuid.UUID, *, success: bool) -> None:
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is None:
                return
            run.status = "completed" if success else "failed"
            run.completed_at = _now()
            usage = dict(run.state.get("model_usage") or {}) if run.state else {}
            run.model_usage = usage
            run.cost = {"usd": float(usage.get("cost_usd", 0.0) or 0.0), "currency": "usd"}
            owner_uuid = run.owner_id
            session_id = run.session_id
        if self.event_bus is not None:
            event_type = EventTypes.RUN_COMPLETED if success else EventTypes.RUN_FAILED
            await self.event_bus.publish(
                DomainEvent(
                    type=event_type,
                    owner_id=owner_uuid,
                    run_id=run_id,
                    session_id=session_id,
                    payload={"status": "completed" if success else "failed"},
                )
            )

    async def _fail(self, run_id: uuid.UUID, exc: BaseException) -> None:
        code, message = self._classify_error(exc)
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is None:
                return
            run.status = "failed"
            run.error = {
                "code": code,
                "message": message,
                "detail": getattr(exc, "detail", None),
            }
            run.completed_at = _now()
            owner_uuid = run.owner_id
            session_id = run.session_id
        if self.event_bus is not None:
            await self.event_bus.publish(
                DomainEvent(
                    type=EventTypes.RUN_FAILED,
                    owner_id=owner_uuid,
                    run_id=run_id,
                    session_id=session_id,
                    payload={"code": code, "message": message},
                )
            )

    @staticmethod
    def _classify_error(exc: BaseException) -> tuple[str, str]:
        if isinstance(exc, AgentOSError):
            code = exc.code
            if isinstance(code, ErrorCode):
                code = code.value
            return str(code), str(exc)
        return ErrorCode.INVALID_STATE.value, str(exc)

    async def _get_run_row(self, run_id: uuid.UUID) -> Run | None:
        async with session_scope() as session:
            return await session.get(Run, run_id)
