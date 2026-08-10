# Department 01 — Agent Runtime

> **职责**：实现 Agent 的核心执行状态机 — 接收用户输入，构建上下文，决策下一步动作，调用工具，生成回复，提交记忆。所有 Run 必须持久化、可恢复、可取消。
> **优先级**：P0（Foundation）
> **预估规模**：L
> **负责目录**：`packages/agent_runtime/`

---

## 1. 部门边界

### 你负责
- LangGraph 状态机构建与节点实现
- AgentState 数据结构
- Task 分类（L0 Chat → L4 Proactive）
- Plan 生成与执行（L2 Multi-step, L3 Long-running）
- Run 生命周期：创建、执行、暂停、恢复、取消
- Checkpoint 持久化（通过 LangGraph Checkpointer → PostgreSQL）
- 流式事件发射（SSE 事件）
- 错误分类与重试策略
- 幂等键生成

### 你不负责
- 具体的 Context 组装逻辑 → Department 06
- 具体的 Tool 执行逻辑 → Department 03
- 具体的 Policy 判断逻辑 → Department 04
- 具体的 Memory 读写逻辑 → Department 05
- 模型调用 → Department 10
- Gateway/Channel 适配 → Department 02

### 你的上游依赖
- `ModelProvider` 接口（Department 10）
- `ToolBroker` 接口（Department 03）
- `MemoryStore` 接口（Department 05）
- `ContextEngine` 接口（Department 06）
- `PolicyEngine` 接口（Department 04）

### 你提供给下游
- `AgentState` 数据结构
- Run 生命周期事件（DomainEvent）
- SSE 事件流

---

## 2. 核心状态机

以下状态机是实现的核心：

```
[*] → Intake → ContextBuild → Decide
                                  ├── Respond (no action) → Reflect → MemoryCommit → [*]
                                  ├── Plan (complex task) → ToolRequest
                                  └── ToolRequest (simple tool)
                                        → PolicyCheck
                                            ├── Execute (allow)
                                            ├── Approval (ask) → Execute / Cancelled
                                            └── Blocked (deny) → Respond
                                        → Observe → Decide
```

### 2.1 必须实现的状态节点

| 节点 | 说明 |
|------|------|
| `intake` | 标准化用户输入，创建/恢复 Run |
| `build_context` | 调用 ContextEngine 组装上下文 |
| `decide` | 调用 ModelRouter 决定：回复 / 规划 / 直接工具调用 |
| `plan` | 对 L2/L3 任务生成执行计划 |
| `tool_request` | 构造 Tool Call → 调用 ToolBroker（含 PolicyCheck） |
| `execute` | ToolBroker 返回 allow 后执行 |
| `observe` | 处理工具返回结果，注入到消息历史 |
| `respond` | 生成最终回复 |
| `reflect` | 异步提取 Memory Candidates / Skill Candidates |
| `memory_commit` | 调用 MemoryStore 写入记忆 |

---

## 3. 任务分类（必须实现）

在所有任务进入 Planning 之前，先用 Task Classifier 分级：

| 级别 | 名称 | 特征 | 处理方式 |
|------|------|------|----------|
| L0 | Chat | 闲聊、知识问答 | Context → Model → Answer，不生成 Plan |
| L1 | One-shot Tool | "明天有什么安排？" | Context → Tool Selection → Execute → Answer |
| L2 | Multi-step | "帮我整理这个项目并写学习指南" | 生成显式 Plan → 逐步执行 |
| L3 | Long-running | "调查最近一个月的 Agent Security 产品" | Plan + Checkpoint + Resumable + Progress Events |
| L4 | Proactive/Recurring | "每周五生成周报" | 交给 Scheduler（Department 08） |

---

## 4. AgentState（必须实现的完整结构）

```python
from typing import TypedDict, Literal

class AgentState(TypedDict):
    run_id: str
    session_id: str
    owner_id: str
    agent_id: str

    # Input
    user_input: str

    # Message history
    messages: list  # List of {role, content, tool_calls, tool_call_id, ...}

    # Context (populated by ContextEngine)
    context_items: list  # List of {content, source, trust, type, ...}

    # Task & Planning
    task: dict | None  # {level: Literal["L0","L1","L2","L3","L4"], classification_reason: str}
    plan: list[dict]  # [{id, objective, success_criteria, status, tool_name, arguments, result, error}]
    current_step: int

    # Tool execution
    pending_tool_call: dict | None  # {name, arguments, risk_level}
    tool_results: list  # [{tool_name, arguments, result, error, latency_ms}]

    # Approval
    pending_approval: dict | None  # {approval_id, tool_name, risk_level, reason}

    # Memory
    memory_candidates: list  # [{content, type, confidence, importance}]

    # Status
    status: Literal[
        "intake", "building_context", "deciding",
        "planning", "executing_tool", "waiting_approval",
        "responding", "reflecting", "committing_memory",
        "completed", "failed", "cancelled", "paused"
    ]
```

**约束**：State 中保存结构化数据，不要保存大量拼装后的 Prompt 字符串。Prompt 组装是 ContextEngine 的职责。

---

## 5. LangGraph 实现骨架

```python
# packages/agent_runtime/graph.py

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from .state import AgentState

def build_graph(
    context_engine,   # ContextEngine protocol
    model_provider,   # ModelProvider protocol
    tool_broker,      # ToolBroker protocol
    memory_store,     # MemoryStore protocol
) -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("intake", intake)
    graph.add_node("build_context", build_context)
    graph.add_node("decide", decide)
    graph.add_node("plan", plan)
    graph.add_node("tool_request", tool_request)
    graph.add_node("execute", execute)
    graph.add_node("observe", observe)
    graph.add_node("respond", respond)
    graph.add_node("reflect", reflect)
    graph.add_node("memory_commit", memory_commit)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "build_context")
    graph.add_edge("build_context", "decide")

    # Conditional routing from decide
    graph.add_conditional_edges("decide", route_decision, {
        "respond": "respond",
        "plan": "plan",
        "tool_request": "tool_request",
    })

    graph.add_edge("plan", "tool_request")
    graph.add_edge("tool_request", "execute")  # Policy check happens inside tool_broker
    graph.add_edge("execute", "observe")
    graph.add_edge("observe", "decide")  # Loop back

    graph.add_edge("respond", "reflect")
    graph.add_edge("reflect", "memory_commit")
    graph.add_edge("memory_commit", END)

    return graph


def route_decision(state: AgentState) -> str:
    """Route based on model's decision."""
    task = state.get("task", {})
    level = task.get("level", "L0")

    if level == "L0":
        return "respond"
    elif level in ("L2", "L3"):
        return "plan"
    elif level == "L1":
        return "tool_request"
    else:
        return "respond"
```

**关键约束**：`execute` 节点不直接 import 任何 connector。它只调用 `tool_broker.execute(...)`。

---

## 6. Durable Execution（Checkpoint）

```
LangGraph PostgresSaver
    +
PostgreSQL
```

支持：
- 服务重启后恢复未完成的 Run
- 用户暂停 → 稍后恢复
- Human-in-the-loop（等待审批）
- 失败重试（从最近的 checkpoint 恢复）

### 实现要点
- 每次进入新节点时 LangGraph 自动保存 checkpoint
- `interrupt` 用于等待 Approval
- `resume` 用于用户审批后继续

---

## 7. 错误分类与重试

### 7.1 错误分类（Error Taxonomy）

```python
class RunError(Exception):
    code: str  # MODEL_ERROR | TOOL_ERROR | AUTH_ERROR | POLICY_DENIED |
               # APPROVAL_REJECTED | TIMEOUT | RATE_LIMIT | SANDBOX_ERROR |
               # CONNECTOR_ERROR | INVALID_STATE | USER_CANCELLED
    recoverable: bool
    retry_strategy: str  # "none" | "immediate" | "backoff" | "ask_user"
```

### 7.2 重试策略

| 场景 | 策略 |
|------|------|
| API 429 Rate Limit | 指数退避重试（max 3 次） |
| Validation Error | 不重试，直接返回错误 |
| Permission Denied | 请求用户授权，不自动重试 |
| Destructive Tool 部分成功 | 绝不盲目重试 |
| Timeout | 一次重试，更长的 timeout |

---

## 8. 幂等性

所有有副作用的 Tool Call 必须携带 `idempotency_key`（基于 `run_id + step_id + tool_name + argument_hash`）。

```python
import hashlib, json

def idempotency_key(run_id: str, step_id: str, tool_name: str, arguments: dict) -> str:
    payload = f"{run_id}:{step_id}:{tool_name}:{json.dumps(arguments, sort_keys=True)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:32]
```

---

## 9. 流式事件

必须发射以下 SSE 事件：

| 事件类型 | 触发时机 |
|----------|----------|
| `run.started` | Run 开始 |
| `text.delta` | 模型逐 token 输出 |
| `tool.requested` | Agent 请求调用工具 |
| `tool.started` | 工具开始执行 |
| `tool.result` | 工具执行完成 |
| `approval.required` | 需要用户审批 |
| `artifact.created` | 生成 Artifact |
| `run.completed` | Run 成功完成 |
| `run.failed` | Run 失败 |
| `run.cancelled` | Run 被取消 |

---

## 10. 详细任务列表

### Task 1: 定义 AgentState
- [ ] 完整实现 `AgentState` TypedDict
- [ ] 定义所有 Literal 类型的合法值
- [ ] 编写 State 验证逻辑（不允许将大段 Prompt 字符串存入 state）

### Task 2: 实现 Task Classifier
- [ ] 实现 L0-L4 分类逻辑
- [ ] 分类 Prompt 独立可测试
- [ ] 测试用例：至少覆盖所有 5 个级别

### Task 3: 构建 LangGraph
- [ ] 实现所有 10 个节点函数
- [ ] 实现 conditional routing
- [ ] 所有节点通过依赖注入获取外部服务（不硬编码 import）
- [ ] 单元测试：mock 所有外部依赖，测试每个节点

### Task 4: 实现 Planner
- [ ] L2 Plan 生成：`{goal, steps: [{id, objective, success_criteria}]}`
- [ ] L3 Plan 生成：额外包含 checkpoint 策略
- [ ] Plan 持久化到 `runs.state` JSONB
- [ ] 支持 Plan 中间失败后从当前 step 继续

### Task 5: PostgreSQL Checkpoint
- [ ] 配置 LangGraph PostgresSaver
- [ ] 测试：创建 Run → 重启服务 → Run 仍可恢复
- [ ] 测试：暂停 Run → 恢复 Run → 继续执行

### Task 6: Run 生命周期 API
- [ ] `POST /v1/runs` — 创建并开始 Run
- [ ] `GET /v1/runs/{id}` — 查询 Run 状态
- [ ] `POST /v1/runs/{id}/cancel` — 取消 Run
- [ ] `POST /v1/runs/{id}/resume` — 恢复 Run（含审批通过后的 resume）

### Task 7: 错误处理框架
- [ ] 实现 `RunError` 异常体系
- [ ] 实现按错误类型的重试策略
- [ ] 实现 idempotency key 生成

### Task 8: SSE Streaming
- [ ] 实现 SSE 事件流端点
- [ ] 所有关键节点 emit 事件
- [ ] 测试：客户端能收到完整事件序列

---

## 11. 验收标准

### 端到端测试
```text
POST /v1/sessions/{id}/messages {"text": "你好"}
    ↓
Run 创建（status: completed）
    ↓
消息持久化到 messages 表
    ↓
SSE 事件完整：run.started → text.delta* → run.completed
```

### Durable Execution 测试
```text
POST /v1/sessions/{id}/messages {"text": "帮我搜索 Python 教程"}
    ↓
Run 执行到 tool_request → 暂停
    ↓
重启 API 服务
    ↓
GET /v1/runs/{id} → 状态仍为 waiting_approval（或 running）
```

### 取消测试
```text
POST /v1/runs/{id}/cancel
    ↓
Run status 变为 cancelled
    ↓
当前正在执行的 tool 收到取消信号
```

---

## 12. 参考蓝图章节

- Section 7: Agent Runtime 状态机
- Section 8: Planner / Executor
- Section 32: Durable Execution
- Section 72: Error Taxonomy
- Section 73: Retry Policy
- Section 74: Idempotency
- Section 42: Streaming
- Section 96: 第一个 Runtime Skeleton
