# Personal AI OS — 开发全量报告

> **用途**：本报告是 Personal AI OS 项目的完整开发交付说明，供独立模型全面审查项目时使用。
> **报告日期**：2026-08-11
> **Git**：28 commits，工作树干净，主分支 `main`

---

## 1. 项目概述

### 1.1 目标（来自设计蓝图）
构建一个 **长期运行、拥有持久身份与记忆、能够安全调用外部能力、可暂停/恢复任务、可主动执行工作、且所有重要行为可审计的个人 Agent Runtime**（即"Personal AI OS"）。不是聊天机器人，而是 Agent 操作系统。

### 1.2 本次交付范围（MVP 垂直切片）
聚焦蓝图 §93-§97 的核心闭环：**Chat → Session → Run → Context → Model → ToolBroker → Policy/Approval → Memory → SSE → DB 持久化**。完整实现了 Runtime / Context / Tool / Policy / Approval / Memory / Model Gateway / API / Gateway / CLI / Observability 的核心链路。

### 1.3 技术栈（实际使用）
| 层 | 选型 |
|---|---|
| 语言 | Python 3.12+（实际运行 3.13） |
| Web | FastAPI + uvicorn + sse-starlette |
| Agent 状态机 | LangGraph 1.2（StateGraph + interrupt/Command 实现 HITL） |
| ORM/DB | SQLAlchemy 2 async + aiosqlite（测试/dev）/ asyncpg + PostgreSQL 16 + pgvector（生产） |
| LLM | Anthropic Messages API（provider adapter）；无 Key 时回退 EchoProvider（demo 模式） |
| 工具调用 | httpx、ast（安全表达式求值） |
| 测试 | pytest + pytest-asyncio；ruff 静态检查 |

---

## 2. 仓库结构与代码规模

```
├── src/personal_ai_os/          # 核心包（11 个子包 + common + db）
│   ├── common/                  # 跨部门契约：数据模型、Protocol、工具函数
│   ├── db/                      # SQLAlchemy ORM（16 张表）+ async 会话工厂
│   ├── agent_runtime/           # LangGraph 状态机 + Run 生命周期
│   ├── context_engine/          # 4 层 Prompt 组装 + 上下文预算 + trust 隔离
│   ├── model_gateway/           # ModelProvider 抽象 + 路由 + failover + embedding
│   ├── tool_broker/             # ToolRegistry + ToolBroker（唯一执行入口）
│   ├── policy_engine/           # R0-R4 策略 + 审批引擎 + 凭据代理
│   ├── memory_engine/           # 记忆持久化 + 混合检索 + 规则抽取
│   ├── gateway/                 # 会话路由 + ServiceContainer（DI 根）
│   ├── scheduler/               # 事件总线（EventBus）
│   └── observability/           # 审计日志 + Eval 框架
├── connectors/                  # 内置连接器（calculator / filesystem / http_fetch）
├── apps/api/                    # FastAPI 应用 + 全部 REST/SSE 路由
├── apps/cli/                    # 命令行客户端
├── tests/                       # 33 个测试文件，281 个测试
│   ├── unit/                    # 32 个文件
│   └── integration/             # 1 个垂直切片端到端
├── migrations/                  # Alembic（async env）
├── evals/datasets/              # Eval 数据集
├── deploy/                      # Dockerfile.api + docker-compose.yml
├── scripts/smoke.sh             # 可复现端到端冒烟测试
└── docs/
    ├── dept/                    # 12 份部门工作指南 + 主编排指南（规划产物）
    ├── dept/INTEGRATION_NOTES.md
    ├── dept/CODE_REVIEW.md
    └── adr/                     # 架构决策记录
```

**规模**：67 个源码文件，约 8,600 行 Python（不含测试）。

---

## 3. 跨部门接口契约（`common/`）

所有模块通过 `common/models.py` 的数据模型与 `common/protocols.py` 的 Protocol 进行依赖注入，**无跨部门直接 import 具体实现**（依赖倒置原则）。

核心模型（均为轻量类，非 pydantic，避免重依赖）：
- `AgentState`（TypedDict）：run/session/owner 身份、messages、context_items、task/plan、pending_tool_call、tool_results、pending_approval、memory_candidates、status
- `ToolDescriptor`：name/namespace/description/input_schema/risk_level(0-4)/side_effect/destructive/idempotent/credential_scope/timeout_seconds
- `ToolResult` / `ToolExecutionContext`：统一工具执行契约
- `PolicyDecision`：allow/ask/deny + risk_level + reasons + constraints
- `ApprovalRequest` / `ApprovalReceipt`（含 `argument_hash` —— 安全关键）
- `ModelRequest` / `ModelResponse` / `ModelUsage`：purpose/messages/tools/latency_class/quality_class
- `MemoryCreate` / `Memory` / `MemoryQuery`：带 provenance（source_type/source_id）
- `DomainEvent` + `EventTypes`：事件总线契约
- `TrustLevel`：trusted_user/system/tool + untrusted_web/email/document/mcp
- `ErrorCode`：11 类错误分类（MODEL_ERROR/TOOL_ERROR/POLICY_DENIED/APPROVAL_REJECTED/TIMEOUT/...）

核心 Protocol（`protocols.py`）：
- `ModelProvider.complete()` / `MemoryStore.search()` / `MemoryStore.write()`
- `PolicyEngine.evaluate()` / `ApprovalEngine` / `CredentialBroker.inject()`
- `Connector.list_tools()` / `Connector.execute()`
- `ContextEngine.build()` / `EventBus`

工具函数（`common/utils.py`）：
- `idempotency_key()` / `argument_hash()`（审批 hash 绑定）
- `LogSanitizer.sanitize_dict()`（密钥脱敏，SECRET_KEYS 匹配 + 模式匹配）
- `utc_now()` / `ensure_aware()`（跨方言 datetime 规范化）
- `untrusted_wrapper()`（prompt injection 隔离标记）

---

## 4. 各模块实现说明

### 4.1 Agent Runtime（`agent_runtime/`）
LangGraph 状态机，节点：`intake → build_context → decide → (plan|tool_request → approval → execute) → observe → decide(循环) → respond → reflect → memory_commit`。

- **Task 分类**（L0-L4）：规则优先，可选模型路径（失败回退规则）
- **Planner**：L2/L3 生成 2-4 步计划
- **HITL 审批**：`tool_request` 节点遇 `ApprovalRequiredError` 只记录 `pending_approval`；独立的 `approval` 节点把 `interrupt()` 作为**首个副作用语句**（LangGraph resume 时 replay 安全，拒绝时绝不执行工具）
- **RunRunner**：Run/Message/RunStep/ToolCall 持久化、生命周期事件发布、`resume` 统一处理审批解析 + `Command(resume=...)` 续跑
- **持久化去重**：ToolCall 审计行以 idempotency_key/approval_id 幂等（broker 与 runner 双写者去重）

### 4.2 Context Engine（`context_engine/`）
- **4 层 Prompt**：Tier1 稳定（identity/security/tool_policy，cache 友好）→ Tier2 半稳定（profile/skills）→ Tier3 动态（记忆/任务态/对话窗口）→ Tier4 易变（当前输入/工具结果）
- **ContextBudget**：按蓝图 §13.2 百分比分配 + generation reserve
- **Trust 隔离**：untrusted 内容用 `--- DATA START/END ---` 包裹，放 user 消息（绝不进 system），明确标注"是数据不是指令"
- 模板文件：`prompts/identity.md` / `security.md` / `tool_policy.md`

### 4.3 Tool System（`tool_broker/`）
- **ToolRegistry**：注册/查询/搜索（关键词）/`list_for_llm`（按需加载 tool schema，避免全量注入）
- **ToolBroker.execute()** 完整流水线：registry 查找 → JSON Schema 校验 → Policy 判断 → 凭据注入 → 连接器执行（超时）→ 结果脱敏/截断 → 事件发布 → ToolCall 审计落库
- **审批边界（安全关键）**：policy=="ask" 且 `ctx.approval_id` 存在时调用 `verify_approval()` 校验精确参数 hash —— **"批准 A 执行 B"被阻断**；校验失败返回 POLICY_DENIED + 审计 `approval.mismatch_blocked`
- **`_secrets` 隔离**：凭据注入后从参数剥离，绝不让 secret 到达连接器/日志

### 4.4 连接器（`connectors/`）
- **calculator**：`ast` 白名单安全求值（仅数字/算术/白名单数学函数），拒绝属性访问/dunder/字符串/非白名单调用，节点数与指数上限
- **filesystem**：realpath + commonpath 路径越狱校验（防 `../`）、敏感文件拒读（.env/*.pem/*id_rsa*）、1MB 上限
- **http_fetch**：scheme 校验、域名 allowlist、SSRF 防护（IP 字面量 + DNS 解析检测私有地址）、禁重定向、超时与响应大小限制

### 4.5 Policy / Approval / Credentials（`policy_engine/`）
- **PolicyEngine**：双层规则匹配 —— specific 规则（`tool_name_pattern`/namespace，如 `shell.*`→ask、`credential.*`→deny）优先于 risk-band 规则（r0-auto…r4-strict）；无匹配回退 R0-2 allow / R3-4 ask
- **ApprovalEngine**：DB 持久化审批请求、过期自动判定、resolve 生成 ApprovalReceipt 并**把参数 hash 绑定到精确参数**（含编辑后参数）、`verify()`/`verify_approval()` 执行前校验
- **CredentialBroker**：env/source 解析，`_secrets[scope]={"token":...}` 信封注入（LogSanitizer 可脱敏），缺失抛 AuthError

### 4.6 Memory Engine（`memory_engine/`）
- **SQLMemoryStore**：provenance 强制（source_type 必填）、精确去重、软删 forget（清 links + 审计事件）、owner 隔离
- **混合检索**：score = 0.4·cosine + 0.2·keyword + 0.2·importance + 0.2·recency（时间衰减，跨方言 aware UTC）
- **DeterministicEmbedding**：无外部 API 的确定性哈希向量（dim=384，CJK 按字符分词），保证测试可复现；生产可换真实 embedding
- **RuleBasedExtractor**：从对话提取偏好候选（"我喜欢/我用/以后/记住" 等模式）

### 4.7 Model Gateway（`model_gateway/`）
- **AnthropicProvider**：真实 Messages API 适配（system 提取、tool_use/tool_result 转换、cache 与 cost 解析、上游错误统一 → ModelError）
- **ModelRouter**：purpose→role（router/worker/vision/embedding）→ 配置模型选择；failover 上限 2 个 provider
- **FakeProvider / EchoProvider**：测试脚本化 / 无 Key demo
- **cost**：per-model 定价表估算（input/cached/output）

### 4.8 API 层（`apps/api/`）
- **create_app(services) DI 工厂**：测试注入 mock，不触发真实模块
- **认证**：`X-API-Key` 头（缺失→dev key；未知 key→401）
- **Owner 隔离**：所有资源查询按 `owner_id == user.id` 过滤（sessions/runs/memories/approvals/automations/audit）
- **SSE**：`GET /v1/runs/{id}/stream` 重放 RunSteps 为事件流
- **路由**：sessions/messages/runs/memories/tools/approvals/automations/audit/stream 全部实现

### 4.9 Gateway / Scheduler / Observability
- **SessionRouter**：channel + conversation_key → 找/建 active session
- **EventBus**：并发分发、handler 异常隔离、sync/async handler 兼容
- **AuditLogger**：append-only AuditEvent + `audit.logged` 事件；ToolBroker 已接入（tool.executed/denied/approval.mismatch_blocked）
- **EvalRunner**：tool_selection / security / task_success 三类评估 + 数据集

---

## 5. 数据库 Schema（16 张表）

users, agents, external_identities, projects, sessions, messages, runs, run_steps, tool_calls, approvals, memories, memory_links, skills, automations, artifacts, audit_events

关键设计决策：
- `sessions.active_run_id` 为**纯 UUID 列（无 FK）**——打破与 `runs.session_id` 的循环外键（PostgreSQL 无法在 FK 环中建/删表）
- 所有 datetime 列默认 **aware UTC**（`_utcnow()`），跨 SQLite/PostgreSQL 一致
- `run.state` JSONB 保存完整 AgentState（durable execution 基础）

---

## 6. 开发过程（多 Agent 工作树流程）

1. **规划**：把设计蓝图拆为 12 份部门工作指南 + 主编排指南（`docs/dept/`），定义 8 个跨部门接口契约
2. **并行开发**：主 Agent 派 4 个独立子 Agent，各在独立 git 工作树（`feat/runtime` / `feat/tools` / `feat/model-policy-memory` / `feat/api`）并行实现并自测
   - Runtime：55 测试；Tools：104；Model/Policy/Memory：66；API：43
3. **合并集成**：主 Agent 逐一审查差异、解决冲突（如 `tests/conftest.py`）、合并
4. **对抗性审查**：每个大项完成后攻击者视角找 bug 并修复
5. **全量测试** + **Code Review**

---

## 7. 测试与验证

### 7.1 测试统计
- **281 个测试全部通过，零失败零警告**（pytest）
- 33 个测试文件：32 单元 + 1 垂直切片集成
- ruff 静态检查全部通过
- 覆盖：calculator 注入、filesystem 逃逸、http SSRF、审批 hash 绑定、参数防篡改、策略拒绝、owner 隔离、并发不串扰、信任标签隔离、跨方言 datetime

### 7.2 关键集成测试（`tests/integration/test_vertical_slice.py`，7 个 e2e）
真实 ToolBroker + Policy + Approval + Graph + Runner 全链路（无 mock）：
- L0 聊天端到端（Run + messages 持久化）
- L1 calculator 工具流（R0 自动执行 + ToolCall 落库）
- **审批通过流**（R3 → ask → 暂停 → 批准 → verify → 执行）
- **审批拒绝流**（拒绝 → 工具不执行 → Run 完成）
- **审批参数不匹配阻断**（批准 A 执行 B → 阻止）
- **策略拒绝**（credential.* → POLICY_DENIED）
- **记忆流**（偏好抽取 → 持久化 → 检索召回）

### 7.3 跨方言验证
- **SQLite**（测试默认）：281 全过
- **PostgreSQL 16 + pgvector**（真实 Docker 容器）：schema 建/删、MemoryStore、Approval hash 绑定、Policy、完整 Agent 栈端到端（calculator 6*7=42 → ToolCall 持久化）全部通过

### 7.4 HTTP 冒烟测试（`scripts/smoke.sh`，可复现）
真实启动 FastAPI 服务器：health → 6 工具可列出 → 建 session → 发消息 → Run completed（intake→context→decide→respond→reflect→memory_commit）→ SSE 7 事件 → 错误 key 401。

---

## 8. 对抗性审查发现并修复的缺陷（10+ 个真实 bug）

| # | 严重度 | 问题 | 修复 |
|---|--------|------|------|
| 1 | CRITICAL | 审批"批准 A 执行 B"hash 绑定未在 broker 边界落地 | ToolBroker 注入审批引擎，执行前 `verify_approval` |
| 2 | CRITICAL | 审批批准后重新执行触发无限审批环 | resume 携带 approval_id，broker 校验通过即执行 |
| 3 | HIGH | 同一工具调用被 broker 与 runner 各写一条 ToolCall | 以 idempotency_key/approval_id 去重 |
| 4 | HIGH | 审批参数为 dict 时解析失败 → hash 绑定失效 | 兼容 dict/JSON 字符串 |
| 5 | HIGH | PostgreSQL 循环外键（runs↔sessions）无法建/删表 | 去 active_run_id 的 FK |
| 6 | HIGH | naive/aware datetime 跨方言 TypeError | `utc_now()`/`ensure_aware()` 统一规范化 |
| 7 | MEDIUM | 默认服务接线失配（连接器未注册/方法名错/字段不一致） | `complete_wiring()` + 修正契约 |
| 8 | MEDIUM | 工具执行/拒绝未写审计日志（/v1/audit 空） | AuditLogger 接入 ToolBroker |
| 9 | LOW | `datetime.utcnow()` Python 3.12 弃用（435 警告） | 全部改为 aware UTC |
| 10 | LOW | pytest-asyncio fixture loop scope 未显式配置 | 显式设为 function |

**已确认安全（对抗性验证）**：owner 隔离（无 IDOR）、审批 hash 绑定、calculator ast 注入防护、filesystem 路径穿越、http SSRF、凭据 `_secrets` 剥离、prompt injection DATA 隔离、认证 401。

---

## 9. 已知局限与遗留工作（非 MVP 阻塞）

1. **Scheduler 执行**：automations CRUD 已实现，`run` 端点返回 501（需接 Scheduler/Temporal）
2. **Memory embedding**：MVP 用确定性哈希向量；生产应接真实 embedding model（接口已抽象）
3. **http_fetch DNS rebinding**：SSRF 检查有 TOCTOU 窗口，生产需网络级 allowlist
4. **Web 前端**：`apps/web` 未实现（当前 API + CLI + SSE 可用）
5. **Redis/队列**：多 Worker / 事件重放时引入 Redis Streams
6. **P1/P2 模块**：MCP、Skills、Sandbox、Browser 未实现（契约与设计就绪）
7. **Alembic**：配置就绪但未生成首版迁移脚本（当前用 `create_all`）
8. **成本上限**：`ModelRequest.max_cost` 未在 provider 强制执行（仅记录）

---

## 10. 运行方式

```bash
# 安装
uv sync --extra dev

# 配置（可选：不设 ANTHROPIC_API_KEY 则用 EchoProvider demo 模式）
cp .env.example .env   # 设置 ANTHROPIC_API_KEY / DATABASE_URL

# 启动 API（默认 SQLite；设 DATABASE_URL 可连 PostgreSQL）
uv run python -m apps.api.main
# → http://localhost:8000  (OpenAPI: /docs)

# CLI 对话
export PERSONAL_AI_API_KEY=dev-key
uv run python -m apps.cli.main chat

# 测试
uv run pytest -v                 # 281 tests
bash scripts/smoke.sh            # 端到端冒烟

# PostgreSQL（Docker）
docker run -d --name paos-pg -e POSTGRES_DB=personal_ai_os -e POSTGRES_USER=user \
  -e POSTGRES_PASSWORD=pass -p 5432:5432 pgvector/pgvector:pg16
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/personal_ai_os uv run python -m apps.api.main
```

---

## 11. 供审查模型的重点关注清单

审查时建议优先关注以下文件/方面：
1. **安全边界**：`tool_broker/broker.py`（审批 hash 校验、`_secrets` 剥离）、`policy_engine/approval.py`（hash 绑定）、`policy_engine/engine.py`（规则匹配）
2. **状态机正确性**：`agent_runtime/graph.py`（approval 节点 replay 安全）、`agent_runtime/runner.py`（持久化去重、resume 幂等）
3. **Prompt injection 隔离**：`context_engine/engine.py`（trust 标签处理）
4. **连接器安全**：`connectors/calculator`（ast 白名单）、`connectors/filesystem`（路径校验）、`connectors/http_fetch`（SSRF）
5. **跨方言**：`db/models.py`（FK 环）、`db/session.py`（SQLite/PG 双引擎）
6. **Owner 隔离**：`apps/api/routers/*.py`（全部按 owner 过滤）
7. **测试完备性**：`tests/integration/test_vertical_slice.py`（真实全链路）

---

*本报告基于当前 `main` 分支（HEAD 2452d2a）生成，与代码库实际状态一致。*
