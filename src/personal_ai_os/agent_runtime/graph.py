"""LangGraph state machine for the Agent Runtime (blueprint §7.1).

Graph topology::

    START → intake → build_context → decide ─ conditional ─→ respond | plan | tool_request
    plan → tool_request → approval ─ conditional ─→ execute | respond
    execute → observe → decide   (tool loop)
    respond → reflect → memory_commit → END

All nodes are async ``(state: AgentState) -> dict``. External services (context
engine, model provider, tool broker, memory store, classifier, planner) are
injected through :func:`build_graph` — no concrete connector is ever imported.

Human-in-the-loop approval is handled with ``langgraph.types.interrupt``.
``tool_request`` calls the tool broker; on :class:`ApprovalRequiredError` it
records ``pending_approval`` and returns ``waiting_approval`` WITHOUT executing
the tool. The separate :func:`approval` node then calls ``interrupt(...)`` — its
only side-effecting statement — so it is replay-safe: LangGraph re-runs node
code that precedes an ``interrupt`` on resume, and no side-effecting call sits
in front of it. The runner creates the approval record from the interrupt
payload; a later ``Command(resume=...)`` continues the graph.

The state schema extends the shared :class:`AgentState` with a few internal
channels (``context``, ``pending_response``, ``final_response``) that are not
part of the public contract.
"""

from __future__ import annotations

import json
import logging
import uuid
from functools import partial
from typing import Any
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from personal_ai_os.agent_runtime.classifier import TaskClassifier
from personal_ai_os.agent_runtime.planner import Planner
from personal_ai_os.agent_runtime.streams import push as push_live
from personal_ai_os.common.models import (
    AgentState,
    ApprovalRequiredError,
    MemoryCreate,
    ModelRequest,
    ModelResponse,
    ToolExecutionContext,
)
from personal_ai_os.common.utils import idempotency_key

logger = logging.getLogger(__name__)


class RuntimeState(AgentState, total=False):
    """Graph-internal channels on top of the public AgentState contract."""

    context: dict
    pending_response: dict
    final_response: str
    #: chain-of-thought from the latest model turn (DeepSeek reasoning, etc.)
    thinking: str
    #: cached context build from build_context (avoids rebuilds in decide)
    _cached_context: dict


# ---------------------------------------------------------------------------
# Injected dependency bundle
# ---------------------------------------------------------------------------


class _Deps:
    __slots__ = ("context_engine", "model_provider", "tool_broker",
                 "memory_store", "classifier", "planner")

    def __init__(self, **kwargs: Any) -> None:
        for key in self.__slots__:
            setattr(self, key, kwargs.get(key))


# ---------------------------------------------------------------------------
# Node helpers
# ---------------------------------------------------------------------------


def _merge_usage(current: dict | None, usage: Any) -> dict:
    merged = dict(current or {})
    if usage is None:
        return merged
    if hasattr(usage, "to_dict"):
        incoming = usage.to_dict()
    elif isinstance(usage, dict):
        incoming = usage
    else:
        return merged
    for key, value in incoming.items():
        if isinstance(value, (int, float)) and value > 0:
            merged[key] = merged.get(key, 0) + value
        else:
            merged[key] = value
    return merged


async def _available_tool_schemas(tool_broker: Any, state: dict) -> list[dict]:
    """Best-effort list of LLM tool schemas from the injected tool broker."""
    list_method = getattr(tool_broker, "list_available_tools", None) or getattr(tool_broker, "list_tools", None)
    if list_method is None:
        return []
    try:
        # Must be called keyword-style — broker method signature is keyword-only (*).
        tools = list_method(owner_id=state.get("owner_id"))
        if hasattr(tools, "__await__"):
            tools = await tools
        schemas: list[dict] = []
        for tool in tools or []:
            if hasattr(tool, "to_llm_schema"):
                schemas.append(tool.to_llm_schema())
            elif isinstance(tool, dict):
                schemas.append(tool)
        return schemas
    except Exception:
        logger.warning("Failed to list available tool schemas", exc_info=True)
        return []


async def _model_call(deps: _Deps, state: dict, request: ModelRequest) -> ModelResponse:
    """Call the model, streaming deltas to the run's live SSE when available.

    When a live stream is registered for this run *and* the provider supports
    ``stream``, token deltas (thinking + text) are pushed live while the full
    :class:`ModelResponse` is accumulated. Otherwise it falls back to a plain
    ``complete()`` call (e.g. tests / providers without streaming).
    """
    run_id = str(state.get("run_id") or "")
    stream_method = getattr(deps.model_provider, "stream", None)

    from personal_ai_os.agent_runtime import streams

    if run_id and stream_method is not None and streams.get(run_id) is not None:
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[dict] | None = None
        usage = None
        try:
            async for ev in stream_method(request):
                if ev.type == "thinking_delta" and ev.text:
                    thinking_parts.append(ev.text)
                    push_live(run_id, "thinking.delta", {"text": ev.text})
                elif ev.type == "text_delta" and ev.text:
                    content_parts.append(ev.text)
                    push_live(run_id, "text.delta", {"text": ev.text})
                elif ev.type == "tool_call" and ev.tool_call:
                    tool_calls = ev.tool_call
                    first = tool_calls[0] if isinstance(tool_calls, list) else tool_calls
                    fn = first.get("function", first) if isinstance(first, dict) else {}
                    push_live(
                        run_id, "tool.requested",
                        {"tool_name": fn.get("name", ""), "tool_call": first},
                    )
                elif ev.type == "done":
                    usage = ev.usage
        except Exception:  # fall back to non-streaming on any stream failure
            logger.warning("Model streaming failed for run %s; retrying non-streaming", run_id, exc_info=True)
            return await deps.model_provider.complete(request)
        response = ModelResponse.from_stream(
            model=request.preferred_model or "model",
            provider=getattr(deps.model_provider, "provider_name", "provider"),
            content_parts=content_parts,
            thinking_parts=thinking_parts,
            tool_calls=tool_calls,
            usage=usage,
        )
        # DeepSeek reasoning models require reasoning_content to be passed back
        # verbatim on replay — keep it on the tool_call so observe can restore it.
        if response.thinking and response.tool_calls:
            for tc in response.tool_calls:
                tc["reasoning_content"] = response.thinking
        return response

    return await deps.model_provider.complete(request)


def _extract_tool_call_fields(pending: Any) -> tuple[str, dict]:
    """Parse ``{name, arguments}`` out of an LLM-style tool-call dict."""
    if not isinstance(pending, dict):
        return "", {}
    function = pending.get("function") if isinstance(pending.get("function"), dict) else {}
    name = function.get("name") or pending.get("name") or ""
    raw_args = function.get("arguments") or pending.get("arguments") or "{}"
    if isinstance(raw_args, dict):
        arguments = raw_args
    else:
        try:
            arguments = json.loads(raw_args or "{}")
        except Exception:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    return str(name), arguments


def _tool_message_content(entry: dict) -> str:
    text = entry.get("text")
    if text:
        return text
    data = entry.get("data")
    if data:
        return json.dumps(data, ensure_ascii=False)
    return str(entry.get("error") or entry.get("content") or "")


def _serialize_tool_result(result: Any, pending: Any, name: str, ctx: ToolExecutionContext,
                           arguments: dict | None = None, tool_trust: str | None = None) -> dict:
    if hasattr(result, "to_dict"):
        data = result.to_dict()
    elif isinstance(result, dict):
        data = dict(result)
    else:
        data = {"text": str(result)}
    # Honour the tool descriptor's result_trust; default to trusted_tool for
    # built-in connectors that don't surface external content.
    trust = tool_trust or "trusted_tool"
    data.update(
        {
            "id": str(uuid.uuid4()),
            "tool_name": name,
            "arguments": arguments or {},
            "tool_call_id": pending.get("id") if isinstance(pending, dict) else None,
            "tool_call": pending,
            "trust": trust,
            "idempotency_key": ctx.idempotency_key,
        }
    )
    if ctx.approval_id is not None:
        data["approval_id"] = str(ctx.approval_id)
    return data


def _serialize_rejection(name: str, pending: Any, reason: str,
                         approval_id: str | None = None, arguments: dict | None = None) -> dict:
    entry = {
        "id": str(uuid.uuid4()),
        "tool_name": name,
        "arguments": arguments or {},
        "success": False,
        "error": f"Approval rejected: {reason}",
        "error_code": "APPROVAL_REJECTED",
        "text": "",
        "data": None,
        "tool_call_id": pending.get("id") if isinstance(pending, dict) else None,
        "tool_call": pending,
        "trust": "trusted_tool",
    }
    if approval_id:
        entry["approval_id"] = approval_id
    return entry


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def intake(state: AgentState, deps: _Deps) -> dict:
    """Initialize run identity and classify the task level."""
    task = await deps.classifier.classify(state.get("user_input", ""))
    return {
        "run_id": state.get("run_id") or str(uuid.uuid4()),
        "session_id": state.get("session_id"),
        "owner_id": state.get("owner_id"),
        "agent_id": state.get("agent_id"),
        "status": "intake",
        "task": task,
        "messages": state.get("messages") or [],
        "context_items": state.get("context_items") or [],
        "tool_results": state.get("tool_results") or [],
        "plan": state.get("plan") or [],
        "current_step": state.get("current_step", 0),
        "memory_candidates": state.get("memory_candidates") or [],
        "skill_candidates": state.get("skill_candidates") or [],
        "model_usage": state.get("model_usage") or {},
    }


async def build_context(state: AgentState, deps: _Deps) -> dict:
    """Assemble the 4-tier context via the injected ContextEngine.

    The full built result (including messages) is cached in state so
    downstream nodes like ``decide`` don't rebuild the same context.
    """
    built = await deps.context_engine.build(state)
    return {
        "status": "building_context",
        "context_items": built.get("context_items") or [],
        "_cached_context": built,  # avoid redundant rebuild in decide
    }


async def decide(state: AgentState, deps: _Deps) -> dict:
    """Ask the model for the next action (respond / plan / tool call).

    Reuses the cached context from ``build_context`` when available, falling
    back to a fresh build only when the cache is absent (e.g. observe loop
    where state has changed).
    """
    cached = state.get("_cached_context")
    if cached is not None and isinstance(cached, dict) and cached.get("messages"):
        built = cached
    else:
        built = await deps.context_engine.build(state)
    tools = await _available_tool_schemas(deps.tool_broker, state)
    request = ModelRequest(
        purpose="assistant",
        messages=built["messages"],
        tools=tools or None,
    )
    response = await _model_call(deps, state, request)
    usage = _merge_usage(state.get("model_usage"), response.usage)

    tool_calls = response.tool_calls
    if tool_calls:
        pending = tool_calls[0] if isinstance(tool_calls, list) else tool_calls
        return {
            "status": "deciding",
            "pending_tool_call": pending,
            "pending_response": None,
            "model_usage": usage,
        }
    return {
        "status": "deciding",
        "pending_tool_call": None,
        "pending_response": {
            "content": response.content,
            "thinking": response.thinking,
            "model": response.model,
            "provider": response.provider,
            "usage": response.usage.to_dict() if response.usage else None,
        },
        "model_usage": usage,
    }


def route_after_decide(state: AgentState) -> str:
    """Route: respond | plan | tool_request."""
    if state.get("pending_tool_call"):
        level = (state.get("task") or {}).get("level", "L0")
        if level in ("L2", "L3") and not state.get("plan"):
            return "plan"
        return "tool_request"
    return "respond"


async def plan(state: AgentState, deps: _Deps) -> dict:
    """Generate a plan for L2/L3 tasks before executing tools."""
    steps = await deps.planner.create_plan(state.get("user_input", ""), dict(state))
    return {"plan": steps, "current_step": 0, "status": "planning"}


async def tool_request(state: AgentState, deps: _Deps) -> dict:
    """Execute the pending tool call, or surface it for human approval.

    Execution itself happens here when no approval is required. When the tool
    broker raises :class:`ApprovalRequiredError`, the call is *not* executed —
    the node records ``pending_approval`` and returns ``waiting_approval``; the
    replay-safe :func:`approval` node owns the ``interrupt``. This split keeps
    every node deterministic up to its ``interrupt()`` (LangGraph replays node
    code that precedes an interrupt on resume, so no side-effecting call may
    sit in front of one).
    """
    pending = state.get("pending_tool_call")
    name, arguments = _extract_tool_call_fields(pending)
    run_uuid = UUID(str(state.get("run_id") or uuid.uuid4()))
    owner_uuid = UUID(str(state.get("owner_id") or uuid.uuid4()))
    session_uuid = UUID(str(state["session_id"])) if state.get("session_id") else None
    ctx = ToolExecutionContext(
        run_id=run_uuid,
        session_id=session_uuid,
        owner_id=owner_uuid,
        agent_id=UUID(str(state["agent_id"])) if state.get("agent_id") else None,
        idempotency_key=idempotency_key(str(run_uuid), state.get("current_step", 0), name, arguments),
    )
    existing = list(state.get("tool_results") or [])

    try:
        result = await deps.tool_broker.execute(name, arguments, ctx)
    except ApprovalRequiredError as exc:
        return {
            "status": "waiting_approval",
            "pending_approval": {
                "approval_id": str(exc.request_id),
                "tool_name": exc.tool_name,
                "risk_level": exc.risk_level,
                "reason": exc.reason,
            },
        }

    # Resolve the tool's declared result trust level so the context engine
    # can wrap untrusted content (e.g. web fetches) in DATA markers.
    tool = deps.tool_broker._registry.get(name) if hasattr(deps.tool_broker, "_registry") else None
    tool_trust = getattr(tool, "result_trust", None) if tool is not None else None

    return {
        "status": "executing_tool",
        "pending_tool_call": None,
        "pending_approval": None,
        "tool_results": existing + [_serialize_tool_result(result, pending, name, ctx, arguments, tool_trust)],
    }


async def approval(state: AgentState, deps: _Deps) -> dict:
    """Human-in-the-loop approval gate (replay-safe).

    ``interrupt`` is the first side-effecting statement in this node; everything
    before it is a pure read of ``pending_approval``, so LangGraph's resume
    replay re-runs it harmlessly and the resume decision is consumed correctly.
    """
    pending_approval = state.get("pending_approval") or {}
    pending = state.get("pending_tool_call")
    name, arguments = _extract_tool_call_fields(pending)

    decision = interrupt(
        {
            "type": "approval",
            "approval_id": pending_approval.get("approval_id"),
            "run_id": state.get("run_id"),
            "tool_name": pending_approval.get("tool_name") or name,
            "risk_level": pending_approval.get("risk_level", 0),
            "reason": pending_approval.get("reason", ""),
        }
    )

    approval_id = pending_approval.get("approval_id")
    run_uuid = UUID(str(state.get("run_id") or uuid.uuid4()))
    owner_uuid = UUID(str(state.get("owner_id") or uuid.uuid4()))
    session_uuid = UUID(str(state["session_id"])) if state.get("session_id") else None

    if not (isinstance(decision, dict) and decision.get("decision") == "approved"):
        rejected = _serialize_rejection(
            name or str(pending_approval.get("tool_name") or ""),
            pending,
            str(pending_approval.get("reason") or "user rejected"),
            approval_id,
            arguments,
        )
        return {
            "status": "cancelled",
            "pending_tool_call": None,
            "pending_approval": None,
            "pending_response": {
                "content": (
                    f"已取消调用工具 {pending_approval.get('tool_name') or name}："
                    f"审批未通过。原因：{pending_approval.get('reason') or '用户拒绝'}"
                ),
                "model": "system",
                "provider": "system",
                "usage": None,
            },
            "tool_results": list(state.get("tool_results") or []) + [rejected],
        }

    if decision.get("edited_arguments"):
        arguments = decision["edited_arguments"]
    ctx = ToolExecutionContext(
        run_id=run_uuid,
        session_id=session_uuid,
        owner_id=owner_uuid,
        agent_id=UUID(str(state["agent_id"])) if state.get("agent_id") else None,
        idempotency_key=idempotency_key(str(run_uuid), state.get("current_step", 0), name, arguments),
        approved_by=UUID(str(decision["approved_by"])) if decision.get("approved_by") else None,
        approval_id=UUID(approval_id) if approval_id else None,
    )
    result = await deps.tool_broker.execute(name, arguments, ctx)
    tool = deps.tool_broker._registry.get(name) if hasattr(deps.tool_broker, "_registry") else None
    tool_trust = getattr(tool, "result_trust", None) if tool is not None else None
    return {
        "status": "executing_tool",
        "pending_tool_call": None,
        "pending_approval": None,
        "tool_results": list(state.get("tool_results") or [])
        + [_serialize_tool_result(result, pending, name, ctx, arguments, tool_trust)],
    }


async def execute(state: AgentState, deps: _Deps) -> dict:
    """Record the tool-execution step (execution happens in the preceding node).

    Kept as a distinct node so the graph topology matches the blueprint and so
    the runner has a clean point to persist a ``RunStep`` for tool execution.
    """
    return {"status": "executing_tool", "current_step": (state.get("current_step") or 0) + 1}


async def observe(state: AgentState, deps: _Deps) -> dict:
    """Fold the latest tool result into the message history, then loop to decide."""
    tool_results = state.get("tool_results") or []
    if not tool_results:
        return {"status": "observing"}
    latest = tool_results[-1]
    messages = list(state.get("messages") or [])
    tool_call = latest.get("tool_call")
    if tool_call:
        assistant_frame: dict = {"role": "assistant", "content": None, "tool_calls": [tool_call]}
        # Preserve the provider's reasoning content (DeepSeek reasoning models
        # require it to be passed back verbatim on replay).
        if isinstance(tool_call, dict) and tool_call.get("reasoning_content"):
            assistant_frame["reasoning_content"] = tool_call["reasoning_content"]
        messages.append(assistant_frame)
    messages.append(
        {
            "role": "tool",
            "content": _tool_message_content(latest),
            "tool_call_id": latest.get("tool_call_id"),
            "name": latest.get("tool_name"),
        }
    )
    return {"status": "observing", "messages": messages}


async def respond(state: AgentState, deps: _Deps) -> dict:
    """Produce the final assistant message (usually from ``pending_response``)."""
    pending = state.get("pending_response") or {}
    content = pending.get("content")
    thinking = pending.get("thinking")
    usage = dict(state.get("model_usage") or {})
    if not content:
        # Fallback: generate directly (e.g. rejected approval path without content).
        cached = state.get("_cached_context")
        if cached is not None and isinstance(cached, dict) and cached.get("messages"):
            built = cached
        else:
            built = await deps.context_engine.build(state)
        response = await _model_call(
            deps, state, ModelRequest(purpose="respond", messages=built["messages"])
        )
        content = response.content
        thinking = response.thinking
        usage = _merge_usage(usage, response.usage)
        pending = {
            "content": content,
            "thinking": thinking,
            "model": response.model,
            "provider": response.provider,
            "usage": response.usage.to_dict() if response.usage else None,
        }
    messages = list(state.get("messages") or []) + [
        {"role": "assistant", "content": content}
    ]
    return {
        "status": "responding",
        "messages": messages,
        "pending_response": None,
        "final_response": content,
        "thinking": thinking,
        "model_usage": usage,
    }


_PREFERENCE_MARKERS = ("我喜欢", "我用", "我希望", "记得", "以后",
                       "I like", "I use", "I prefer", "I want", "prefer")


async def reflect(state: AgentState, deps: _Deps) -> dict:
    """Extract memory candidates from the turn (MVP rule-based)."""
    candidates: list[dict] = []
    user_texts: list[str] = []
    current_input = state.get("user_input")
    if current_input:
        user_texts.append(current_input)
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, dict) and msg.get("role") == "user" and msg.get("content"):
            user_texts.append(str(msg["content"]))
    for text in user_texts:
        for marker in _PREFERENCE_MARKERS:
            if marker in text:
                candidates.append(
                    {
                        "content": text.strip(),
                        "type": "preference",
                        "confidence": 0.7,
                        "importance": 0.5,
                        "source_type": "conversation",
                    }
                )
                break
        if len(candidates) >= 5:
            break
    return {"status": "reflecting", "memory_candidates": candidates, "skill_candidates": []}


async def memory_commit(state: AgentState, deps: _Deps) -> dict:
    """Persist memory candidates via the injected MemoryStore."""
    candidates = state.get("memory_candidates") or []
    if deps.memory_store is not None and candidates:
        owner_uuid = UUID(str(state["owner_id"]))
        for candidate in candidates[:10]:
            memory = MemoryCreate(
                owner_id=owner_uuid,
                agent_id=UUID(str(state["agent_id"])) if state.get("agent_id") else None,
                type=candidate.get("type", "preference"),
                scope=candidate.get("scope", "user"),
                content=candidate.get("content", ""),
                summary=candidate.get("summary"),
                importance=float(candidate.get("importance", 0.5)),
                confidence=float(candidate.get("confidence", 0.5)),
                source_type=candidate.get("source_type", "conversation"),
            )
            await deps.memory_store.write(memory)
    return {"status": "committing_memory"}


def route_after_tool_request(state: AgentState) -> str:
    """Approval needed → approval node; otherwise proceed to execute."""
    if state.get("status") == "waiting_approval":
        return "approval"
    return "execute"


def route_after_approval(state: AgentState) -> str:
    """Rejected approval goes straight to respond; approved proceeds to execute."""
    if state.get("status") == "cancelled":
        return "respond"
    return "execute"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_graph(
    *,
    context_engine: Any,
    model_provider: Any,
    tool_broker: Any,
    memory_store: Any | None = None,
    classifier: Any | None = None,
    planner: Any | None = None,
) -> StateGraph:
    """Build the agent state machine with all services injected."""
    deps = _Deps(
        context_engine=context_engine,
        model_provider=model_provider,
        tool_broker=tool_broker,
        memory_store=memory_store,
        classifier=classifier or TaskClassifier(),
        planner=planner or Planner(),
    )

    graph = StateGraph(RuntimeState)

    graph.add_node("intake", partial(intake, deps=deps))
    graph.add_node("build_context", partial(build_context, deps=deps))
    graph.add_node("decide", partial(decide, deps=deps))
    graph.add_node("plan", partial(plan, deps=deps))
    graph.add_node("tool_request", partial(tool_request, deps=deps))
    graph.add_node("approval", partial(approval, deps=deps))
    graph.add_node("execute", partial(execute, deps=deps))
    graph.add_node("observe", partial(observe, deps=deps))
    graph.add_node("respond", partial(respond, deps=deps))
    graph.add_node("reflect", partial(reflect, deps=deps))
    graph.add_node("memory_commit", partial(memory_commit, deps=deps))

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "build_context")
    graph.add_edge("build_context", "decide")

    graph.add_conditional_edges(
        "decide",
        route_after_decide,
        {"respond": "respond", "plan": "plan", "tool_request": "tool_request"},
    )

    graph.add_edge("plan", "tool_request")
    graph.add_conditional_edges(
        "tool_request",
        route_after_tool_request,
        {"execute": "execute", "approval": "approval"},
    )
    graph.add_conditional_edges(
        "approval",
        route_after_approval,
        {"execute": "execute", "respond": "respond"},
    )
    graph.add_edge("execute", "observe")
    graph.add_edge("observe", "decide")

    graph.add_edge("respond", "reflect")
    graph.add_edge("reflect", "memory_commit")
    graph.add_edge("memory_commit", END)

    return graph


def compile_graph(*, checkpointer: Any | None = None, **kwargs: Any) -> Any:
    """Convenience: ``build_graph(...)`` then ``.compile(checkpointer)``."""
    graph = build_graph(**kwargs)
    return graph.compile(checkpointer=checkpointer)
