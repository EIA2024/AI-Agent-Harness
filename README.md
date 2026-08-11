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

# 2. 配置 LLM Provider（OpenAI/Anthropic/DeepSeek/...，支持多套切换）
uv run python -m apps.cli.main config init
#    → 交互式向导：选择请求格式（OpenAI 兼容 / Anthropic）、Base URL、API Key、模型
#    → 配置保存在 ~/.personal_ai/profiles.json（仓库外，不会进入 git）

# 3. 启动 API（默认 SQLite）
uv run python -m apps.api.main
# → http://localhost:8000  (docs at /docs)

# 4. 使用 CLI 对话（另开终端）
export PERSONAL_AI_API_URL=http://localhost:8000
export PERSONAL_AI_API_KEY=dev-key
uv run python -m apps.cli.main chat
```

> 未配置任何 Provider 时系统以 EchoProvider（demo 回显）模式运行，链路完整但 Agent 不会真正推理。

### 多套 Provider 切换

```bash
uv run python -m apps.cli.main config list          # 查看已保存的配置
uv run python -m apps.cli.main config show          # 查看当前生效的配置
uv run python -m apps.cli.main config use <name>    # 切换到另一套
uv run python -m apps.cli.main config edit <name>   # 修改 URL/模型/Key
uv run python -m apps.cli.main config remove <name> # 删除
# 切换后重启 API 服务生效
```

支持的格式：`openai`（OpenAI / DeepSeek / Moonshot / GLM / Qwen / Ollama 等任何 chat-completions 兼容服务）与 `anthropic`（Claude 官方）。

### 记忆与 Provider 切换（调研结论）

**切换聊天模型（OpenAI ⇄ Anthropic ⇄ DeepSeek 等）不会丢失任何记忆。** 长期记忆存储在本地数据库（`memories` 表）的 `memories`/`memory_links` 表，与 LLM 厂商无关——换 Provider 只是换"大脑"，记忆仍在。

一个需要了解的细节是**嵌入向量**：检索用向量做相似度排序。本系统 MVP 用**确定性哈希嵌入**（不依赖任何外部 API，可复现），因此切换 Provider 完全无影响。若未来接入真实嵌入模型，需注意**不同嵌入模型的向量空间不兼容**——切换嵌入模型需对存量记忆重嵌入（行业共识，见 [Qdrant 嵌入迁移指南](https://qdrant.tech/documentation/tutorials-operations/embedding-model-migration/)、[Mixpeek 嵌入可移植性](https://mixpeek.com/guides/embedding-portability-versioning)）。因当前不持久化向量、检索时实时计算，切换不会损坏数据，只是相似度口径可能变化。

**结论：记忆在 Provider 之间完全共享；切换聊天 API 是安全的。**

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
