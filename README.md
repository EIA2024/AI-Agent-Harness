# Personal AI OS

一个**长期运行、拥有持久身份与记忆、能够安全调用外部能力、可暂停/恢复任务、可主动执行工作、并且所有重要行为都可审计的个人 Agent Runtime**。

完整设计见 [Personal_AI_OS_Development_Blueprint_v1.md](Personal_AI_OS_Development_Blueprint_v1.md)，部门工作指南见 [docs/dept/](docs/dept/)。

## 架构概览

```
User → Gateway/Session → Agent Runtime (LangGraph)
                              ├── Context Engine ──→ Model Gateway (multi-provider)
                              ├── Tool Broker ──→ Policy/Approval ──→ Connectors
                              ├── Memory Engine (provenance + hybrid retrieval)
                              └── durable Run state (Postgres / SQLite)
```

- **Runtime**：LangGraph 状态机（intake → context → decide → tool → respond → memory commit），支持 HITL 审批与恢复
- **安全**：Policy Engine（R0-R4 风险分级）→ Approval（参数 hash 绑定）→ Credential Broker（Secret 隔离注入）
- **记忆**：五层记忆，带 provenance（来源追踪），混合检索（关键词 + 语义 + 加权）
- **模型无关**：ModelProvider 抽象 + 路由 + failover + 成本记录

## 快速开始

```bash
# 1. 安装依赖
uv sync --extra dev

# 2. 配置（复制并按需修改）
cp .env.example .env

# 3. 启动 API（默认 SQLite）
uv run python -m apps.api.main
# → http://localhost:8000  (docs at /docs)

# 4. 使用 CLI 对话（另开终端）
export PERSONAL_AI_API_URL=http://localhost:8000
export PERSONAL_AI_API_KEY=dev-key
uv run python -m apps.cli.main chat
```

> 无 LLM API Key 时，可将模型指向 Echo/Fake provider（见 `model_gateway`），系统仍可完整跑通状态机与工具链路。

## 运行测试

```bash
uv run pytest -v
```

## 仓库布局

```text
apps/                     # API (FastAPI)、CLI
src/personal_ai_os/
  agent_runtime/          # LangGraph 状态机、Run 生命周期
  context_engine/         # Prompt 分层组装、上下文预算
  model_gateway/          # 模型抽象、路由、failover、成本
  policy_engine/          # 策略引擎、审批引擎、凭据代理
  tool_broker/            # 工具注册表、执行代理
  memory_engine/          # 记忆存储、混合检索、抽取
  gateway/                # 会话路由
  scheduler/              # 事件总线、自动化
  observability/          # 审计日志
  common/                 # 跨部门契约（模型、协议、工具）
  db/                     # SQLAlchemy ORM + 会话工厂
connectors/               # 原生连接器（calculator/filesystem/http_fetch）
migrations/               # Alembic
docs/dept/                # 部门工作指南
```

## 安全约束（不可协商）

1. Agent 不直接持有权限，一切能力调用经过 Tool Broker
2. 所有副作用操作经过 Policy（R3+ 需人工审批）
3. 长期记忆必须携带 provenance
4. Prompt 不是安全边界，安全边界在 Tool Broker / Policy
5. 外部内容默认 untrusted（标记为数据而非指令）
