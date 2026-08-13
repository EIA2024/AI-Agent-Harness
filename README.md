<div align="center">

# Personal AI OS

**一个长期运行、拥有持久身份与记忆、可安全调用外部能力、可暂停/恢复任务、可主动执行、所有重要行为可审计的个人 Agent Runtime。**

Python · FastAPI · LangGraph · SQLAlchemy · PostgreSQL / SQLite

</div>

---

## ✨ 特性

- **Agent Runtime** — LangGraph 状态机（`intake → context → decide → tool → respond → memory`），任务分类 L0–L4，HITL 审批，durable Run 持久化，SSE 实时流式输出。
- **🔐 安全优先** — R0–R4 策略引擎、审批参数 hash 绑定（"批准 A 执行 B"被阻断）、凭据隔离注入、trust 标签防注入、无界工具循环守卫。
- **🧠 上下文工程** — 4 层 Prompt 组装 + token 预算 + **MemGPT 风格对话压缩**（近期完整 + 历史摘要），"在当前 context 能力下尽量保留对话记忆"。
- **📦 模型无关** — Anthropic + OpenAI 兼容（OpenAI / DeepSeek / Moonshot / GLM / Qwen / Ollama），多套本地 Provider 配置一键切换，failover，成本记录。
- **🔌 工具系统** — 单一 ToolBroker 执行路径；内置 calculator（安全求值）、filesystem（路径隔离）、http_fetch（SSRF 防护 + HTML→文本）。
- **🖥️ 终端体验** — CLI 流式打字机输出、进度提示、工具调用实时宣布；模型私有推理不会写入持久层或终端。

## 📚 文档

| 文档 | 说明 |
|---|---|
| [架构](docs/ARCHITECTURE.md) | 系统设计、模块职责、数据流、安全模型 |
| [API 参考](docs/API.md) | 全部 REST 端点 + SSE 事件 |
| [快速开始](#快速开始) | 5 分钟跑起来 |
| [开发指南](CONTRIBUTING.md) | 贡献规范与设计约束 |

## 快速开始

需要 **Python 3.12+** 与 [uv](https://docs.astral.sh/uv/)。

```bash
# 1. 安装依赖
uv sync --extra dev

# 2. 配置 LLM Provider（OpenAI/Anthropic/DeepSeek/...，支持多套切换）
uv run python -m apps.cli.main config init
#    → 交互式向导：请求格式（OpenAI 兼容 / Anthropic）→ Base URL → API Key → 模型

# 3. 一键启动本地开发环境（首次运行会生成并保存本地 API 密钥）
# macOS / Linux：
./start.sh
# Windows（cmd 或 PowerShell）：
start.bat
# → 迁移 SQLite → 启动 API → 打开交互式 TUI
# → API 文档：http://127.0.0.1:8000/docs

# 4. 仅启动 API（默认本地 SQLite）
export PERSONAL_AI_DEV_API_KEY=replace-with-a-local-secret
uv run python -m apps.api.main
# → http://localhost:8000   （交互式文档 /docs）

# 5. 另开终端，用 CLI 对话
export PERSONAL_AI_API_URL=http://localhost:8000
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

## 环境变量

| 变量 | 必需 | 说明 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 否（用 `config init` 更佳） | Anthropic 密钥（legacy 回退） |
| `PERSONAL_AI_DEV_API_KEY` | 开发服务器访问 | 显式启用本地开发用户；客户端 `X-API-Key` 必须匹配 |
| `PERSONAL_AI_KEY_HASH_SECRET` | 生产必需 | API Key 的 HMAC 哈希密钥；应使用独立随机值 |
| `DATABASE_URL` | 否 | PostgreSQL URL；缺省用本地 SQLite（`data/app.db`） |
| `PERSONAL_AI_CONFIG_DIR` | 否 | Provider 配置目录（默认 `~/.personal_ai`） |
| `PERSONAL_AI_API_URL` / `PERSONAL_AI_API_KEY` | CLI | API 地址及访问密钥；密钥需与服务器配置匹配 |
| `PERSONAL_AI_WORKSPACE_ROOT` | 生产必需 | filesystem 连接器允许访问的唯一根目录 |
| `PERSONAL_AI_HTTP_ALLOWED_DOMAINS` | 生产必需 | http_fetch 允许访问的逗号分隔域名白名单 |

## 记忆与 Provider 切换

**切换聊天模型（OpenAI ⇄ Anthropic ⇄ DeepSeek 等）不会丢失任何记忆。** 长期记忆存于本地数据库，与 LLM 厂商无关。嵌入向量用确定性哈希（不依赖外部 API），切换完全无影响；未来若接真实嵌入模型，需注意不同模型向量空间不兼容（需重嵌入）。

**对话上下文** 采用 MemGPT 式 compaction：近期对话保留完整，更早对话压缩为运行摘要常驻上下文（详见 [架构文档](docs/ARCHITECTURE.md#42-conversation-memory-memgpt-style-compaction)）。

## Docker 部署

```bash
docker compose -f deploy/compose/docker-compose.yml up
# API 仅绑定宿主机 127.0.0.1；Postgres 只在容器内部网络暴露
```

## 运行测试

```bash
uv run pytest -v          # 300+ tests（SQLite 内存）
uv run ruff check src connectors apps tests
bash scripts/smoke.sh     # 端到端冒烟
```

## 仓库布局

```text
apps/                     # API (FastAPI, REST+SSE) / CLI（流式）
connectors/               # 原生连接器（calculator/filesystem/http_fetch）
src/personal_ai_os/
  agent_runtime/          # LangGraph 状态机 + Run 生命周期 + 实时流
  context_engine/         # 4 层 Prompt + 预算 + 对话摘要压缩
  model_gateway/          # 模型 Provider/路由/配置/成本
  policy_engine/          # R0–R4 策略 + 审批 + 凭据
  tool_broker/            # 工具注册表 + 唯一执行路径
  memory_engine/          # 记忆持久化 + 混合检索 + 抽取
  gateway/                # 会话路由 + 依赖注入
  scheduler/              # 事件总线
  observability/          # 审计日志 + Eval
  common/                 # 跨部门契约（模型/协议/工具）
  db/                     # SQLAlchemy ORM + 会话工厂
migrations/               # Alembic
deploy/                   # Docker Compose / Dockerfile
docs/                     # 架构/API/部门工作指南/ADR
evals/                    # 评估数据集
```

## 安全约束（不可协商）

1. Agent 不直接持有权限，一切能力调用经过 Tool Broker
2. 所有副作用操作经过 Policy（R3+ 需人工审批，参数 hash 绑定）
3. 长期记忆必须携带 provenance（来源追踪）
4. Prompt 不是安全边界，安全边界在 Tool Broker / Policy
5. 外部内容默认 untrusted（标记为数据而非指令）

## License

[MIT](LICENSE)

## 致谢

设计参考了 LangGraph、Letta/MemGPT（compaction）、OpenClaw、Hermes、MCP 规范等优秀开源 Agent 工程。
