"""Run lifecycle manager for the Agent Runtime.

The runner owns lifecycle persistence. LangGraph's ``state.status`` is a graph
*phase*, not the durable Run lifecycle; conflating those two previously disabled
session serialization after the first node.  This module therefore keeps DB
lifecycle transitions explicit and fenced by a per-worker lease.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from personal_ai_os.agent_runtime.streams import push as push_live
from personal_ai_os.common.models import AgentOSError, DomainEvent, ErrorCode, EventTypes
from personal_ai_os.common.utils import approximate_tokens
from personal_ai_os.context_engine.summarizer import ConversationSummarizer
from personal_ai_os.db.models import Approval, Message, Run, RunStep, Session, ToolCall
from personal_ai_os.db.session import session_scope
from personal_ai_os.policy_engine.approval import ApprovalNotPendingError

logger = logging.getLogger(__name__)

ACTIVE_RUN_STATUSES = ("running", "waiting_approval")
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled")
_MAX_TOOL_RESULTS_PERSISTED = 50
RECENT_WINDOW_TOKENS = 6000
DEFAULT_LEASE_SECONDS = 90


class SessionBusyError(Exception):
    """A session already owns an active run."""


class RunLeaseLostError(Exception):
    """This worker no longer owns the durable execution lease."""


class RunExecutionCancelled(Exception):
    """The durable run was cancelled while this worker was executing it."""


def _now() -> datetime:
    return datetime.now(UTC)


def _strip_reasoning(state: dict) -> dict:
    """Recursively remove provider-private chain-of-thought before persistence."""
    stripped = dict(state)
    stripped.pop("thinking", None)

    def _clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: _clean(v)
                for k, v in value.items()
                if k not in ("reasoning_content", "thinking")
            }
        if isinstance(value, list):
            return [_clean(item) for item in value]
        return value

    return {k: _clean(v) for k, v in stripped.items()}


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
        worker_id: str | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        self.graph = graph
        self.model_provider = model_provider
        self.tool_broker = tool_broker
        self.memory_store = memory_store
        self.context_engine = context_engine
        self.policy_engine = policy_engine
        self.approval_engine = approval_engine
        self.event_bus = event_bus
        self.classifier = classifier
        self.planner = planner
        self.worker_id = worker_id or f"worker-{uuid.uuid4()}"
        self.lease_seconds = max(
            15,
            int(lease_seconds or os.environ.get("PERSONAL_AI_RUN_LEASE_SECONDS", DEFAULT_LEASE_SECONDS)),
        )

        if hasattr(graph, "astream"):
            self.compiled = graph
        else:
            if checkpointer is None:
                logger.warning(
                    "RunRunner built without a persistent checkpointer — using "
                    "InMemorySaver (test/development only)"
                )
            self.compiled = graph.compile(checkpointer=checkpointer or InMemorySaver())

        self._persisted_tool_ids: dict[str, set[str]] = {}
        self._persisted_message_count: dict[str, int] = {}
        self._bg_tasks: dict[str, asyncio.Task] = {}
        self._summarizer_impl: ConversationSummarizer | None = None

    def _lease_expiry(self) -> datetime:
        return _now() + timedelta(seconds=self.lease_seconds)

    # -------------------------------------------------------- session memory

    async def _load_session_history(
        self, session_id, owner_id, exclude_run_id: uuid.UUID | None = None, limit: int = 30
    ) -> list[dict]:
        async with session_scope() as session:
            stmt = (
                select(Message)
                .where(Message.session_id == session_id, Message.owner_id == owner_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
            rows = list((await session.execute(stmt)).scalars().all())
        history: list[dict] = []
        for message in reversed(rows):
            if exclude_run_id is not None and message.run_id == exclude_run_id:
                continue
            if message.role not in ("user", "assistant"):
                continue
            if message.role == "assistant" and not (message.content or "").strip():
                continue
            history.append({"role": message.role, "content": message.content or ""})
        return history

    def _summarizer(self) -> ConversationSummarizer:
        if self._summarizer_impl is None:
            self._summarizer_impl = ConversationSummarizer(self.model_provider)
        return self._summarizer_impl

    async def _load_session_memory(self, session_id) -> tuple[str, list[dict]]:
        async with session_scope() as session:
            sess = await session.get(Session, session_id)
            if sess is None:
                return "", []
            owner_id = sess.owner_id
            ctx = sess.context or {}
        summary = str(ctx.get("conversation_summary") or "")
        recent = ctx.get("recent_messages") or []
        if isinstance(recent, list) and recent:
            clean = [
                {"role": m.get("role"), "content": m.get("content") or ""}
                for m in recent
                if isinstance(m, dict)
                and m.get("role") in ("user", "assistant")
                and m.get("content")
            ]
            return summary, clean
        return summary, await self._load_session_history(session_id, owner_id)

    async def _save_session_memory(self, session_id, summary: str, recent: list[dict]) -> None:
        async with session_scope() as session:
            sess = await session.get(Session, session_id)
            if sess is None:
                return
            ctx = dict(sess.context or {})
            ctx["conversation_summary"] = summary
            ctx["recent_messages"] = recent[-30:]
            sess.context = ctx

    async def _compact_session_memory(self, run_id) -> None:
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is None or run.session_id is None:
                return
            session_id = run.session_id
            state = dict(run.state or {})
        if not state:
            return
        summary = str(state.get("conversation_summary") or "")
        turns = [
            {"role": m.get("role"), "content": m.get("content") or ""}
            for m in (state.get("messages") or [])
            if isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and m.get("content")
        ]
        total = sum(approximate_tokens(t.get("content") or "") for t in turns)
        if total <= RECENT_WINDOW_TOKENS:
            await self._save_session_memory(session_id, summary, turns)
            return

        keep: list[dict] = []
        acc = 0
        for turn in reversed(turns):
            n = approximate_tokens(turn.get("content") or "")
            if keep and acc + n > RECENT_WINDOW_TOKENS:
                break
            keep.append(turn)
            acc += n
        keep.reverse()
        overflow = turns[: len(turns) - len(keep)]
        if overflow:
            try:
                summary = await self._summarizer().summarize(
                    overflow, existing_summary=summary
                )
            except Exception:  # noqa: BLE001 - compaction must not fail a completed run
                logger.warning("conversation compaction failed", exc_info=True)
        await self._save_session_memory(session_id, summary, keep)

    # --------------------------------------------------------------- leasing

    async def _claim_waiting_run(self, run_id: uuid.UUID) -> None:
        """Atomically transition waiting_approval -> running and claim the lease."""
        now = _now()
        async with session_scope() as session:
            result = await session.execute(
                sa_update(Run)
                .where(
                    Run.id == run_id,
                    Run.status == "waiting_approval",
                    or_(
                        Run.lease_owner.is_(None),
                        Run.lease_owner == self.worker_id,
                        Run.lease_expires_at.is_(None),
                        Run.lease_expires_at < now,
                    ),
                )
                .values(
                    status="running",
                    lease_owner=self.worker_id,
                    lease_expires_at=self._lease_expiry(),
                )
            )
            if result.rowcount != 1:
                raise SessionBusyError(
                    f"run {run_id} is no longer waiting or is owned by another worker"
                )

    async def _renew_or_fence(self, session, run: Run) -> None:  # noqa: ANN001
        if run.status == "cancelled":
            raise RunExecutionCancelled(f"run {run.id} was cancelled")
        if run.status != "running":
            raise RunLeaseLostError(
                f"run {run.id} lifecycle is {run.status!r}; worker cannot execute"
            )
        if run.lease_owner != self.worker_id:
            raise RunLeaseLostError(
                f"run {run.id} lease belongs to {run.lease_owner!r}, not {self.worker_id!r}"
            )
        run.lease_expires_at = self._lease_expiry()
        await session.flush()

    # ---------------------------------------------------------------- start

    async def _init_run(self, *, session_id, owner_id, user_input, agent_id=None):
        run_id = uuid.uuid4()
        owner_uuid = _coerce_uuid(owner_id)
        session_uuid = _coerce_uuid(session_id)
        agent_uuid = _coerce_uuid(agent_id) if agent_id else None
        started = _now()

        summary, recent = await self._load_session_memory(session_uuid)
        current_user = {"role": "user", "content": user_input}
        initial_messages = list(recent) + [current_user]
        run_message_start = len(recent)

        try:
            async with session_scope() as session:
                busy = (
                    await session.execute(
                        select(Run.id)
                        .where(
                            Run.session_id == session_uuid,
                            Run.status.in_(ACTIVE_RUN_STATUSES),
                        )
                        .limit(1)
                    )
                ).scalars().first()
                if busy is not None:
                    raise SessionBusyError(
                        f"session {session_uuid} already has an active run {busy}"
                    )

                run = Run(
                    id=run_id,
                    owner_id=owner_uuid,
                    session_id=session_uuid,
                    agent_id=agent_uuid,
                    status="running",
                    lease_owner=self.worker_id,
                    lease_expires_at=self._lease_expiry(),
                    input={
                        "user_input": user_input,
                        "agent_id": str(agent_uuid) if agent_uuid else None,
                    },
                    state={},
                    model_usage={},
                    cost={},
                    started_at=started,
                )
                session.add(run)
                await session.flush()

                sess = await session.get(Session, session_uuid)
                if sess is not None:
                    sess.active_run_id = run_id
                    sess.last_active_at = started

                session.add(
                    Message(
                        session_id=session_uuid,
                        run_id=run_id,
                        run_seq=0,
                        owner_id=owner_uuid,
                        role="user",
                        content=user_input,
                        metadata_={"channel": "api"},
                    )
                )
        except IntegrityError as exc:
            raise SessionBusyError(
                f"session {session_uuid} acquired an active run concurrently"
            ) from exc

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
            "messages": initial_messages,
            "_run_message_start": run_message_start,
            "conversation_summary": summary,
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
        self._persisted_message_count[str(run_id)] = run_message_start + 1
        return run_id, initial

    async def start(
        self, *, session_id, owner_id, user_input: str, agent_id=None
    ) -> dict:
        run_id, initial = await self._init_run(
            session_id=session_id,
            owner_id=owner_id,
            user_input=user_input,
            agent_id=agent_id,
        )
        config = {"configurable": {"thread_id": str(run_id)}}
        try:
            interrupt_payload = await self._stream(run_id, initial, config)
            if interrupt_payload is not None:
                await self._handle_approval_interrupt(run_id, interrupt_payload)
            elif await self._finalize(run_id, success=True):
                await self._compact_session_memory(run_id)
            return await self.get_run(run_id)
        except (RunExecutionCancelled, RunLeaseLostError):
            return await self.get_run(run_id)
        except Exception as exc:
            await self._fail(run_id, exc)
            raise

    async def start_streaming(
        self, *, session_id, owner_id, user_input: str, agent_id=None
    ) -> dict:
        run_id, initial = await self._init_run(
            session_id=session_id,
            owner_id=owner_id,
            user_input=user_input,
            agent_id=agent_id,
        )
        from personal_ai_os.agent_runtime import streams

        streams.register(run_id)
        push_live(run_id, "run.started", {"run_id": str(run_id)})
        config = {"configurable": {"thread_id": str(run_id)}}
        task = asyncio.create_task(self._run_background(run_id, initial, config))
        self._bg_tasks[str(run_id)] = task
        return {"id": str(run_id), "run_id": str(run_id), "status": "running"}

    async def _run_background(self, run_id: uuid.UUID, initial: dict, config: dict) -> None:
        from personal_ai_os.agent_runtime import event_log, streams

        terminal = "run.completed"
        approval_info: dict | None = None
        try:
            interrupt_payload = await self._stream(run_id, initial, config)
            if interrupt_payload is not None:
                approval_info = await self._handle_approval_interrupt(run_id, interrupt_payload)
                terminal = "approval.required"
            elif await self._finalize(run_id, success=True):
                await self._compact_session_memory(run_id)
            else:
                terminal = "run.cancelled"
        except (RunExecutionCancelled, RunLeaseLostError):
            terminal = "run.cancelled"
        except asyncio.CancelledError:
            terminal = "run.cancelled"
        except Exception as exc:
            await self._fail(run_id, exc)
            terminal = "run.failed"
        finally:
            try:
                current = await self.get_run(run_id)
                status = current.get("status", "failed")
            except Exception:  # noqa: BLE001
                current = {}
                status = "failed"
            if status == "waiting_approval":
                terminal = "approval.required"
            elif status == "failed":
                terminal = "run.failed"
            elif status == "cancelled":
                terminal = "run.cancelled"
            elif status == "completed":
                terminal = "run.completed"

            payload: dict = {"run_id": str(run_id), "status": status}
            final_response = (current.get("state") or {}).get("final_response")
            if final_response:
                payload["final_response"] = final_response
            if approval_info is not None:
                payload.update(approval_info)
            push_live(run_id, terminal, payload)
            await event_log.append_run_event(run_id, terminal, payload)
            streams.unregister(run_id)
            self._bg_tasks.pop(str(run_id), None)

    # ---------------------------------------------------------------- resume

    async def resume(
        self,
        run_id,
        *,
        approval_id=None,
        decision: str | None = None,
        edited_arguments: dict | None = None,
    ) -> dict:
        run_uuid = _coerce_uuid(run_id)
        run_row = await self._get_run_row(run_uuid)
        if run_row is None:
            raise KeyError(f"Run not found: {run_id}")
        if run_row.status != "waiting_approval":
            raise ValueError("Run is not waiting for approval")
        state = dict(run_row.state or {})
        decision = (decision or "approved").lower()
        approved_by = run_row.owner_id

        if approval_id is not None:
            if self.approval_engine is None:
                raise RuntimeError("approval engine is unavailable; refusing unsafe resume")
            try:
                await self.approval_engine.resolve(
                    _coerce_uuid(approval_id),
                    decision=decision,
                    approved_by=approved_by,
                    edited_arguments=edited_arguments,
                )
            except ApprovalNotPendingError:
                # The explicit approval endpoint may already have resolved it.
                pass

        await self._claim_waiting_run(run_uuid)
        resume_value: dict = {"decision": decision, "approved_by": str(approved_by)}
        if edited_arguments:
            resume_value["edited_arguments"] = edited_arguments

        if self.event_bus is not None and approval_id is not None:
            event_type = (
                EventTypes.APPROVAL_APPROVED
                if decision in ("approved", "approved_with_edits")
                else EventTypes.APPROVAL_REJECTED
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
            interrupt_payload = await self._stream(
                run_uuid, state, config, resume_value=resume_value
            )
            if interrupt_payload is not None:
                await self._handle_approval_interrupt(run_uuid, interrupt_payload)
            elif await self._finalize(run_uuid, success=True):
                await self._compact_session_memory(run_uuid)
            return await self.get_run(run_uuid)
        except (RunExecutionCancelled, RunLeaseLostError):
            return await self.get_run(run_uuid)
        except Exception as exc:
            await self._fail(run_uuid, exc)
            raise

    # ---------------------------------------------------------------- cancel

    async def cancel(self, run_id) -> None:
        run_uuid = _coerce_uuid(run_id)
        now = _now()
        async with session_scope() as session:
            run = await session.get(Run, run_uuid)
            if run is None:
                raise KeyError(f"Run not found: {run_id}")
            owner_uuid = run.owner_id
            session_id = run.session_id
            if run.status not in TERMINAL_RUN_STATUSES:
                run.status = "cancelled"
                run.completed_at = now
                run.lease_owner = None
                run.lease_expires_at = None
                state = dict(run.state or {})
                state["status"] = "cancelled"
                run.state = _strip_reasoning(state)
                await session.execute(
                    sa_update(Approval)
                    .where(Approval.run_id == run_uuid, Approval.status == "pending")
                    .values(status="rejected")
                )
                if session_id is not None:
                    await session.execute(
                        sa_update(Session)
                        .where(Session.id == session_id, Session.active_run_id == run_uuid)
                        .values(active_run_id=None)
                    )

        self._cleanup_bookkeeping(str(run_uuid))
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
        from personal_ai_os.agent_runtime import event_log, streams

        payload = {"run_id": str(run_uuid), "status": "cancelled"}
        push_live(run_uuid, "run.cancelled", payload)
        await event_log.append_run_event(run_uuid, "run.cancelled", payload)
        task = self._bg_tasks.pop(str(run_uuid), None)
        if task is not None and not task.done():
            task.cancel()
        streams.unregister(run_uuid)

    async def get_run(self, run_id) -> dict:
        run_uuid = _coerce_uuid(run_id)
        async with session_scope() as session:
            run = await session.get(Run, run_uuid)
            if run is None:
                raise KeyError(f"Run not found: {run_id}")
            return {
                "id": str(run.id),
                "run_id": str(run.id),
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
                    await self._persist_step(
                        run_id, "approval", running, status="waiting_approval"
                    )
                    break
                for node_name, update in chunk.items():
                    running.update(update)
                    await self._persist_step(
                        run_id, node_name, running, update=update
                    )
        except Exception:
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
                raise RunLeaseLostError(f"run {run_id} disappeared")

            if status == "waiting_approval":
                if run.status == "cancelled":
                    raise RunExecutionCancelled(f"run {run_id} was cancelled")
                if run.status != "running" or run.lease_owner != self.worker_id:
                    raise RunLeaseLostError(f"worker lost run {run_id} before approval")
                run.status = "waiting_approval"
                run.lease_owner = None
                run.lease_expires_at = None
            else:
                await self._renew_or_fence(session, run)

            run.state = _strip_reasoning(running)
            if running.get("model_usage"):
                run.model_usage = running["model_usage"]

            phase = status or str(running.get("status") or node_name)
            session.add(
                RunStep(
                    run_id=run_id,
                    step_type=node_name,
                    status=phase,
                    data=_strip_reasoning(dict(update or {})),
                    started_at=_now(),
                    completed_at=_now(),
                )
            )
            await self._persist_messages(session, run_id, running)
            await self._persist_tool_calls(session, run_id, running)

    async def _persist_messages(self, session, run_id: uuid.UUID, running: dict) -> None:  # noqa: ANN001
        messages = running.get("messages") or []
        start_index = int(running.get("_run_message_start", 0) or 0)
        key = str(run_id)
        last = self._persisted_message_count.get(key)
        if last is None:
            # Reconstruct durable progress after process restart/resume.
            max_seq = (
                await session.execute(
                    select(func.max(Message.run_seq)).where(Message.run_id == run_id)
                )
            ).scalar_one_or_none()
            last = start_index + (int(max_seq) + 1 if max_seq is not None else 0)

        run_row = await session.get(Run, run_id)
        if run_row is None:
            return
        for index in range(max(last, start_index), len(messages)):
            message = messages[index]
            run_seq = index - start_index
            session.add(
                Message(
                    session_id=run_row.session_id,
                    run_id=run_id,
                    run_seq=run_seq,
                    owner_id=run_row.owner_id,
                    role=message.get("role") or "assistant",
                    content=message.get("content") or "",
                    tool_name=message.get("name"),
                    metadata_=(
                        {"tool_call_id": message.get("tool_call_id")}
                        if message.get("tool_call_id")
                        else {}
                    ),
                )
            )
        self._persisted_message_count[key] = len(messages)

    async def _persist_tool_calls(self, session, run_id: uuid.UUID, running: dict) -> None:  # noqa: ANN001
        seen = self._persisted_tool_ids.setdefault(str(run_id), set())
        for raw_entry in (running.get("tool_results") or [])[-_MAX_TOOL_RESULTS_PERSISTED:]:
            entry = _strip_reasoning(raw_entry)
            entry_id = entry.get("id")
            if not entry_id or entry_id in seen:
                continue
            seen.add(entry_id)
            success = bool(entry.get("success", True))
            approval_uuid = (
                _coerce_uuid(entry["approval_id"]) if entry.get("approval_id") else None
            )
            idem_key = entry.get("idempotency_key")

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
                # Broker is authoritative for execution rows. Only fill a legacy
                # awaiting-approval row when it has not already reached terminal.
                if existing.status == "awaiting_approval":
                    existing.status = "success" if success else "error"
                    existing.result = entry if success else None
                    existing.error = (
                        {"code": entry.get("error_code"), "message": entry.get("error")}
                        if not success
                        else None
                    )
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
                    continue

            # Runtime pseudo-results (parse/repeat blocks) have no broker row;
            # record them without an idempotency key so they cannot collide with
            # a real execution claim.
            session.add(
                ToolCall(
                    run_id=run_id,
                    tool_name=entry.get("tool_name", ""),
                    arguments=entry.get("arguments") or {},
                    risk_level=int(entry.get("risk_level", 0) or 0),
                    approval_id=approval_uuid,
                    status="success" if success else "error",
                    result=entry if success else None,
                    error=(
                        {"code": entry.get("error_code"), "message": entry.get("error")}
                        if not success
                        else None
                    ),
                    idempotency_key=None,
                    completed_at=_now(),
                )
            )

    async def _handle_approval_interrupt(self, run_id: uuid.UUID, payload: dict) -> dict | None:
        approval_value = payload.get("approval_id")
        if not approval_value:
            return None
        approval_uuid = _coerce_uuid(approval_value)
        tool_name = payload.get("tool_name", "")
        risk_level = int(payload.get("risk_level", 0) or 0)
        reason = payload.get("reason", "")
        requires_auth_method = payload.get("requires_auth_method")

        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is None:
                return None
            state = dict(run.state or {})
            pending_tool_call = state.get("pending_tool_call") or {}
            function = (
                pending_tool_call.get("function")
                if isinstance(pending_tool_call, dict)
                else None
            )
            arguments: dict = {}
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
                        requires_auth_method=requires_auth_method,
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
                "requires_auth_method": requires_auth_method,
            }
            run.state = _strip_reasoning(state)
            run.status = "waiting_approval"
            run.lease_owner = None
            run.lease_expires_at = None
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
                        "requires_auth_method": requires_auth_method,
                    },
                )
            )
        return {
            "approval_id": str(approval_uuid),
            "tool_name": tool_name,
            "risk_level": risk_level,
            "action_summary": f"执行工具 {tool_name}",
            "requires_auth_method": requires_auth_method,
        }

    async def _finalize(self, run_id: uuid.UUID, *, success: bool) -> bool:
        now = _now()
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is None:
                return False
            if run.status in TERMINAL_RUN_STATUSES:
                return run.status == ("completed" if success else "failed")
            if run.status != "running" or run.lease_owner != self.worker_id:
                logger.warning("stale worker refused to finalize run %s", run_id)
                return False

            run.status = "completed" if success else "failed"
            run.completed_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            usage = dict((run.state or {}).get("model_usage") or {})
            run.model_usage = usage
            cost_usd = usage.get("cost_usd")
            run.cost = {
                "usd": float(cost_usd) if cost_usd is not None else None,
                "currency": "usd",
            }
            owner_uuid = run.owner_id
            session_id = run.session_id
            if session_id is not None:
                await session.execute(
                    sa_update(Session)
                    .where(Session.id == session_id, Session.active_run_id == run_id)
                    .values(active_run_id=None)
                )

        self._cleanup_bookkeeping(str(run_id))
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
        return True

    async def _fail(self, run_id: uuid.UUID, exc: BaseException) -> bool:
        code, message = self._classify_error(exc)
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is None or run.status in TERMINAL_RUN_STATUSES:
                return False
            if run.status == "running" and run.lease_owner not in (None, self.worker_id):
                logger.warning("stale worker refused to fail run %s", run_id)
                return False
            run.status = "failed"
            run.error = {
                "code": code,
                "message": message,
                "detail": getattr(exc, "detail", None),
            }
            run.completed_at = _now()
            run.lease_owner = None
            run.lease_expires_at = None
            owner_uuid = run.owner_id
            session_id = run.session_id
            if session_id is not None:
                await session.execute(
                    sa_update(Session)
                    .where(Session.id == session_id, Session.active_run_id == run_id)
                    .values(active_run_id=None)
                )

        self._cleanup_bookkeeping(str(run_id))
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
        return True

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

    def _cleanup_bookkeeping(self, run_id_key: str) -> None:
        self._persisted_tool_ids.pop(run_id_key, None)
        self._persisted_message_count.pop(run_id_key, None)
