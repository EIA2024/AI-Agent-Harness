# Personal AI OS — Master Orchestration Guide

> **用途**：主 Agent 使用本文件调度各子 Agent 完成整个 Personal AI OS 的开发。
> **原则**：各 Department 按依赖顺序推进，先完成接口契约，再各自实现。

---

## 0. 项目一句话定义

构建一个**长期运行、拥有持久身份与记忆、能够安全调用外部能力、可暂停/恢复任务、可主动执行工作、并且所有重要行为都可审计的个人 Agent Runtime**。

---

## 1. 部门总览

| # | Department | 一句话职责 | 优先级 | 预估规模 |
|---|-----------|----------|--------|---------|
| 01 | [Agent Runtime](01_agent_runtime.md) | LangGraph 状态机 + Run 生命周期 | P0 | L |
| 02 | [Gateway & Session](02_gateway_session.md) | 多渠道接入 + 会话路由 | P0 | M |
| 03 | [Tool System & MCP](03_tool_system_mcp.md) | Tool Broker + MCP 集成 | P0 | L |
| 04 | [Security & Trust](04_security_trust.md) | Policy + Approval + Sandbox + Credentials | P0 | L |
| 05 | [Memory System](05_memory_system.md) | 五层记忆架构 + pgvector | P1 | XL |
| 06 | [Context Engine](06_context_engine.md) | Prompt 组装 + 预算管理 | P1 | M |
| 07 | [Skills System](07_skills_system.md) | Skill 生命周期 + 自我改进 | P2 | M |
| 08 | [Scheduler & Autonomy](08_scheduler_autonomy.md) | 定时/延迟/条件触发 + 事件总线 | P2 | M |
| 09 | [Connectors](09_connectors.md) | 原生连接器（GitHub/Gmail/Calendar/...） | P1 | L |
| 10 | [Model Gateway](10_model_gateway.md) | 模型抽象 + 路由 + 成本控制 | P0 | M |
| 11 | [Observability & Evaluation](11_observability_eval.md) | OTel + Metrics + Eval 框架 | P2 | M |
| 12 | [API, Frontend & Infrastructure](12_api_frontend_infra.md) | FastAPI + Next.js + Docker + DB Schema | P0 | XL |

---

## 2. 依赖关系与开发阶段

### 阶段一：Foundation（P0 并行）
所有 P0 部门可以并行启动，但必须**先对齐接口契约**。

```
Agent Runtime ──┐
Gateway/Session ─┤
Tool System ─────┼──→ 先对齐共同接口（AgentState, ToolDescriptor, ModelRequest 等）
Security/Trust ──┤
Model Gateway ───┘
```

### 阶段二：Core Capability（依赖阶段一）
```
Memory System ← 依赖 AgentState、Model Gateway
Context Engine ← 依赖 Memory System、Model Gateway
Connectors ← 依赖 Tool System、Security
API/Frontend/Infra ← 依赖所有后端模块的接口定义
```

### 阶段三：Autonomy（依赖阶段二）
```
Scheduler/Autonomy ← 依赖 Agent Runtime、Event Bus
Skills System ← 依赖 Memory System、Tool System
Observability/Eval ← 依赖所有模块的 trace/metic 埋点
```

---

## 3. 跨部门接口契约（必须优先确定）

以下接口是多个 Department 的共同依赖，必须在各自实现前由主 Agent 确认：

### 3.1 AgentState（Runtime ↔ Context ↔ Memory ↔ Tool）
```python
class AgentState(TypedDict):
    run_id: str
    session_id: str
    owner_id: str
    user_input: str
    messages: list
    context_items: list
    task: dict | None
    plan: list[dict]
    current_step: int
    pending_tool_call: dict | None
    tool_results: list
    pending_approval: dict | None
    memory_candidates: list
    status: str
```

### 3.2 ToolDescriptor（Tool System ↔ Connectors ↔ Security）
```python
class ToolDescriptor(BaseModel):
    name: str
    namespace: str
    description: str
    input_schema: dict
    output_schema: dict | None
    source: Literal["native", "mcp", "plugin", "sandbox"]
    risk_level: int  # R0-R4
    side_effect: bool
    destructive: bool
    external_write: bool
    idempotent: bool
    credential_scope: list[str]
    timeout_seconds: int
```

### 3.3 ModelRequest / ModelResponse（Model Gateway ↔ Runtime ↔ Context）
```python
class ModelRequest(BaseModel):
    purpose: str
    messages: list
    tools: list
    latency_class: str
    quality_class: str
    max_cost: float | None

class ModelResponse(BaseModel):
    content: str | None
    tool_calls: list | None
    usage: dict
    model: str
    finish_reason: str
```

### 3.4 PolicyDecision（Security ↔ Tool System ↔ Runtime）
```python
class PolicyDecision(BaseModel):
    decision: Literal["allow", "ask", "deny"]
    risk_level: int
    reasons: list[str]
    constraints: dict = {}
```

### 3.5 DomainEvent（Scheduler ↔ 所有模块）
```python
class DomainEvent(BaseModel):
    id: UUID
    type: str  # "run.started", "tool.completed", "memory.created", etc.
    timestamp: datetime
    owner_id: UUID
    run_id: UUID | None
    payload: dict
```

### 3.6 Connector Protocol（Connectors ↔ Tool System）
```python
class Connector(Protocol):
    async def list_tools(self) -> list[ToolDescriptor]: ...
    async def execute(self, tool: str, arguments: dict, ctx: ToolExecutionContext) -> ToolResult: ...
```

### 3.7 MemoryStore Protocol（Memory System ↔ Context Engine ↔ Runtime）
```python
class MemoryStore(Protocol):
    async def search(self, query: MemoryQuery) -> list[Memory]: ...
    async def write(self, memory: MemoryCreate) -> Memory: ...
```

### 3.8 ModelProvider Protocol（Model Gateway ↔ Runtime）
```python
class ModelProvider(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...
```

---

## 4. 主 Agent 调度策略

### 4.1 启动顺序
1. 将本文件及所有 Department Guide 分发给对应子 Agent
2. 第一步：要求所有 P0 部门（01-04, 10）的子 Agent 先**输出其模块的公开接口定义**（Python Protocol/BaseModel），不要写实现
3. 主 Agent 审查接口一致性，解决冲突
4. 通过后再让各部门并行实现

### 4.2 日常调度
- 每个 Department 的子 Agent **只能修改其负责的目录**
- 跨部门接口变更需要主 Agent 审批
- 每天/每个里程碑结束时各子 Agent 汇报进度

### 4.3 集成测试
- 集成测试由主 Agent 直接编写（或委托单独的 QA Agent）
- 关键端到端链路：`POST /messages → Session → Run → Context → Model → Tool → Policy → Execute → Memory → Complete`

---

## 5. 不推荐修改的架构决策（ADR）

以下决策已在蓝图中充分论证，各 Department 必须遵守：

1. **LangGraph 为 Agent Runtime**，但 Tool/Memory/Policy 设计为独立服务接口
2. **PostgreSQL + pgvector 为统一存储**，不引入 MongoDB/Qdrant/Neo4j/Redis（第一版）
3. **Tool Broker 是唯一 capability execution path**
4. **MCP 是 Tool protocol，不是 Runtime**
5. **Long-term Memory 必须有 provenance（来源追踪）**
6. **Agent 不直接持有权限** — 必须经过 Tool Broker → Policy → Approval
7. **Prompt 不是安全边界** — 真正的安全边界在 Tool Broker
8. **不做多租户/企业 RBAC** — 但所有核心对象保留 `owner_id`

---

## 6. 技术栈约束

```text
Backend:   Python 3.12+ / FastAPI / Pydantic v2 / SQLAlchemy 2 / Alembic / LangGraph / httpx
Database:  PostgreSQL + pgvector
Frontend:  Next.js / TypeScript / React / Tailwind / shadcn/ui
Streaming: SSE（未来 AG-UI）
Observability: OpenTelemetry / Prometheus（开发阶段 structured JSON logs 即可）
LLM:       自写 provider adapter（多 provider 时引入 LiteLLM）
```

---

## 7. 各 Department 工作指南

详见以下文件：

- [01 — Agent Runtime](01_agent_runtime.md)
- [02 — Gateway & Session](02_gateway_session.md)
- [03 — Tool System & MCP](03_tool_system_mcp.md)
- [04 — Security & Trust](04_security_trust.md)
- [05 — Memory System](05_memory_system.md)
- [06 — Context Engine](06_context_engine.md)
- [07 — Skills System](07_skills_system.md)
- [08 — Scheduler & Autonomy](08_scheduler_autonomy.md)
- [09 — Connectors](09_connectors.md)
- [10 — Model Gateway](10_model_gateway.md)
- [11 — Observability & Evaluation](11_observability_eval.md)
- [12 — API, Frontend & Infrastructure](12_api_frontend_infra.md)
