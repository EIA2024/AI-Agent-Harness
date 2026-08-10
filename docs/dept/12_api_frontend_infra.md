# Department 12 — API, Frontend & Infrastructure

> **职责**：将整个 Personal AI OS 交付给用户 — 包括 REST API 层（FastAPI）、流式传输（SSE）、前端 Web App（Next.js）、数据库 Schema 与迁移（Alembic）、Docker 部署配置。这是所有后端模块的"出口"。
> **优先级**：P0（Foundation）
> **预估规模**：XL
> **负责目录**：`apps/api/` + `apps/web/` + `apps/cli/` + `migrations/` + `deploy/`

---

## 1. 部门边界

### 你负责
- **FastAPI 应用** — 所有 REST 端点实现
- **SSE Streaming** — Agent 响应的实时流式传输
- **数据库迁移** — Alembic + 所有模块的 SQL Schema
- **Next.js 前端** — 6 个核心页面
- **CLI 工具** — 命令行交互入口
- **认证系统** — WebAuthn / OAuth
- **Docker Compose** — 本地开发和生产部署
- **Repository 结构** — 最终的 monorepo 布局

### 你不负责
- 各模块的业务逻辑（你只做路由 + 参数校验 + 调用模块接口）
- Agent 状态的流式事件发射逻辑 → Department 01
- 前端 UI 设计（你可以用 Tailwind/shadcn，Design Dept 负责视觉）→ 见下

### 特殊说明：前端视觉设计
本 Department 负责前端**功能实现**（页面路由、API 对接、状态管理、组件逻辑）。
如需独立的前端视觉设计（品牌色、字体、间距体系），建议额外设立 Design 角色或引入 `frontend-design` skill。

### 你的上游依赖
- 所有 01-11 Department 的公开接口
- Agent Runtime（Department 01）— Run 创建/查询/取消/恢复
- Memory System（Department 05）— Memory CRUD + Search

### 你提供给下游
- REST API（前端和 CLI 使用）
- SSE 事件流（前端实时展示）
- Docker 部署（用户实际运行）

---

## 2. 数据库 Schema（汇总所有 Department 的表）

虽然每个 Department 设计自己的表，但 Alembic migration 由本 Department 统一管理。

### 核心表清单

```text
users                   — 用户账户
agents                  — Agent 定义
external_identities     — 外部渠道身份映射 (Dept 02)

sessions                — 会话 (Dept 02)
messages                — 消息历史
projects                — Project 上下文 (Dept 08)

runs                    — Agent Run (Dept 01)
run_steps               — Run 执行步骤
run_events              — Run 事件流

tool_calls              — 工具调用记录 (Dept 03)
tools                   — 工具注册信息

approvals               — 审批请求 (Dept 04)
credentials_refs        — 凭据引用 (Dept 04)

connections             — OAuth 连接 (Dept 09)
browser_sessions        — 浏览器会话 (Dept 09)
mcp_servers             — MCP Server 注册 (Dept 03)

memories                — 记忆 (Dept 05)
memory_links            — 记忆关系 (Dept 05)

skills                  — 技能 (Dept 07)
skill_versions          — 技能版本 (Dept 07)

automations             — 自动化任务 (Dept 08)
automation_runs         — 自动化运行历史 (Dept 08)

artifacts               — Agent 产出文件 (Dept 08)

audit_events            — 审计日志 (Dept 11)
```

### 核心表 Schema（本 Department 直接实现）

```sql
-- runs (Dept 01)
CREATE TABLE runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES users(id),
    agent_id UUID NOT NULL REFERENCES agents(id),
    session_id UUID REFERENCES sessions(id),
    parent_run_id UUID REFERENCES runs(id),
    status TEXT NOT NULL,
    input JSONB NOT NULL,
    state JSONB NOT NULL DEFAULT '{}',
    model_usage JSONB NOT NULL DEFAULT '{}',
    cost JSONB NOT NULL DEFAULT '{}',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- tool_calls (Dept 03)
CREATE TABLE tool_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id),
    tool_name TEXT NOT NULL,
    arguments JSONB NOT NULL,
    risk_level INTEGER NOT NULL,
    approval_id UUID REFERENCES approvals(id),
    status TEXT NOT NULL,
    result JSONB,
    error JSONB,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- audit_events (Dept 11)
CREATE TABLE audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT,
    event_type TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    details JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 3. FastAPI 应用架构

```text
apps/api/
├── main.py              # FastAPI app 入口
├── config.py            # 配置加载
├── dependencies.py      # 依赖注入（DB session, services）
├── middleware/
│   ├── auth.py          # 认证中间件
│   └── logging.py       # 请求日志
├── routers/
│   ├── sessions.py      # /v1/sessions
│   ├── messages.py      # /v1/sessions/{id}/messages
│   ├── runs.py          # /v1/runs
│   ├── memories.py      # /v1/memories
│   ├── tools.py         # /v1/tools
│   ├── approvals.py     # /v1/approvals
│   ├── automations.py   # /v1/automations
│   ├── artifacts.py     # /v1/artifacts
│   ├── projects.py      # /v1/projects
│   ├── connections.py   # /v1/connections
│   ├── gateway.py       # /gateway/{channel}/webhook
│   └── auth.py          # /auth/*
└── tests/
```

---

## 4. REST API 端点

### Chat & Session

```text
# Sessions
POST   /v1/sessions                        — 创建新 session
GET    /v1/sessions                        — 列出 sessions
GET    /v1/sessions/{id}                   — 获取 session 详情
PATCH  /v1/sessions/{id}                   — 更新 session（切换 project 等）
DELETE /v1/sessions/{id}                   — 归档 session

# Messages（在 session 内发送消息）
POST   /v1/sessions/{id}/messages          — 发送消息（创建 Run）
GET    /v1/sessions/{id}/messages          — 获取消息历史
```

### Runs

```text
GET    /v1/runs/{id}                       — 查询 Run 状态 + state
POST   /v1/runs/{id}/cancel                — 取消 Run
POST   /v1/runs/{id}/resume                — 恢复 Run（审批通过后）
```

### Memory

```text
GET    /v1/memories                        — 列出 memories（支持 filter by type/scope/status）
POST   /v1/memories                        — 手动创建 memory
GET    /v1/memories/{id}                   — 获取单条 memory
PATCH  /v1/memories/{id}                   — 编辑 memory
DELETE /v1/memories/{id}                   — Forget

POST   /v1/memories/search                 — 语义搜索 memories
```

### Tools & Connections

```text
GET    /v1/tools                           — 列出所有可用工具
GET    /v1/tools/{namespace}/{name}        — 获取工具详情
POST   /v1/tools/{namespace}/{name}/test   — 测试调用工具

GET    /v1/connections                     — 列出已连接的服务
POST   /v1/connections/{provider}/auth     — 发起 OAuth
DELETE /v1/connections/{provider}           — 断开连接
```

### Approvals

```text
GET    /v1/approvals                       — 待审批列表
POST   /v1/approvals/{id}/approve          — 批准
POST   /v1/approvals/{id}/reject           — 拒绝
POST   /v1/approvals/{id}/edit             — 编辑后批准（修改参数）
```

### Automations

```text
GET    /v1/automations                     — 列出 automations
POST   /v1/automations                     — 创建 automation
PATCH  /v1/automations/{id}                — 更新 automation
DELETE /v1/automations/{id}                — 删除 automation
POST   /v1/automations/{id}/run            — 手动触发
```

### Gateway

```text
POST   /gateway/{channel}/webhook          — 接收外部 Channel 消息
```

---

## 5. SSE Streaming

### 端点

```text
GET /v1/runs/{id}/stream
```

### 事件格式

```text
event: run.started
data: {"run_id": "...", "timestamp": "..."}

event: text.delta
data: {"text": "我来帮你"}

event: text.delta
data: {"text": "查询一下"}

event: tool.requested
data: {"tool_name": "github.list_commits", "arguments_preview": {...}}

event: tool.result
data: {"tool_name": "github.list_commits", "success": true, "summary": "Found 12 commits"}

event: approval.required
data: {"approval_id": "...", "tool_name": "email.send", "reason": "..."}

event: run.completed
data: {"run_id": "...", "usage": {...}, "cost": {...}}
```

### FastAPI 实现

```python
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

@router.get("/v1/runs/{run_id}/stream")
async def stream_run(run_id: UUID):
    async def event_generator():
        async for event in agent_runtime.stream_events(run_id):
            yield {
                "event": event.type,
                "data": event.model_dump_json(),
            }

    return EventSourceResponse(event_generator())
```

---

## 6. 认证系统

### Personal deployment 推荐方案

```text
WebAuthn / Passkey（首选）
```

或：
```text
OAuth（GitHub / Google 登录）
```

**不要**裸露 Gateway 在公网后只使用简单短密码。

### 实现

```python
# 简单的 API Key 认证（开发阶段）
from fastapi import Depends, HTTPException, Header

async def get_current_user(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await db.execute(
        select(User).where(User.api_key == x_api_key)
    )
    if not user:
        raise HTTPException(status_code=401)
    return user
```

生产化时替换为 WebAuthn/OAuth + JWT session token。

---

## 7. Frontend（Next.js）

### 技术栈

```text
Next.js (App Router)
TypeScript
Tailwind CSS
shadcn/ui (组件库)
SSE client（接收实时 Agent 事件流）
```

### 6 个核心页面

#### 7.1 Chat（首页）
```text
显示: 对话列表、消息气泡、Agent 思考状态
交互: 输入框、附件上传、工具调用展开/折叠
实时: SSE 连接显示 Agent 实时输出
内嵌: Approval 按钮（Approve / Edit / Reject）
```

#### 7.2 Run Inspector
```text
显示: 时间线视图
  └── 09:00 Run started
  └── 09:00 memory.search (234ms) ✓
  └── 09:01 web.search (1.2s) ✓
  └── 09:02 approval required ⏸
  └── 09:04 approved ✓
  └── 09:04 email.send (560ms) ✓
  └── 09:04 completed

交互: 点击展开每个 step 的详细信息（arguments, result, error）
用途: 这是调试 Agent 行为的最重要界面
```

#### 7.3 Memory
```text
显示: 记忆列表（支持搜索/过滤）
每行: content, type, confidence, source, created_at
操作: Edit, Delete (Forget), Pin, Mark incorrect
必须显示: "Where did this memory come from?" (source_type + source_id)
```

#### 7.4 Tools / Connections
```text
显示: 每种服务的连接状态
  GitHub    [Connected]  Permissions: read repo, read issues
  Gmail     [Connected]  Permissions: read
  Calendar  [Not connected]  [Connect]

操作: Connect / Disconnect / Edit permissions
```

#### 7.5 Automations
```text
显示: Automation 列表
  Morning Brief   Schedule: 08:00 daily  Last run: success  Next: 2026-08-11 08:00
  Weekly Report   Schedule: Fri 17:00    Last run: success  Next: 2026-08-14 17:00

操作: Create / Edit / Delete / Run Now / Pause
```

#### 7.6 Security
```text
显示:
  Auto-approved actions（如 R0, R1 工具统计）
  Blocked tools（deny list）
  Trusted MCP servers
  Active credentials（不显示 secret，只显示 label + status）
  Audit events（最近的安全事件时间线）

操作: 修改 Policy 规则、添加 blocked tools、审核 MCP servers
```

---

## 8. CLI

```text
apps/cli/
├── main.py             # CLI 入口（click/typer）
├── session.py          # Session 管理
└── chat.py             # 交互式 Chat
```

### 基本命令

```bash
# 启动交互式对话
personal-ai chat

# 发送单条消息
personal-ai send "帮我检查 GitHub"

# 查看当前 sessions
personal-ai sessions list

# 查看最近的 run
personal-ai runs list --limit 10

# 管理 automation
personal-ai automations list
personal-ai automations create --name "Morning Brief" --cron "0 8 * * *" --prompt "..."

# 管理 memory
personal-ai memories search "Python"
personal-ai memories forget <id>
```

---

## 9. Docker 部署

### Docker Compose（MVP）

```yaml
# deploy/compose/docker-compose.yml
version: "3.8"

services:
  api:
    build:
      context: ../..
      dockerfile: deploy/docker/Dockerfile.api
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://user:pass@postgres:5432/personal_ai_os
      - MODEL_PROVIDER_API_KEY=${ANTHROPIC_API_KEY}
    depends_on:
      - postgres
    volumes:
      - ./data:/app/data

  web:
    build:
      context: ../..
      dockerfile: deploy/docker/Dockerfile.web
    ports:
      - "3000:3000"
    environment:
      - API_URL=http://api:8000

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: personal_ai_os
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

volumes:
  postgres_data:
```

### 第一版只需要 4 个容器

```text
api       — FastAPI 应用
web       — Next.js 前端
postgres  — PostgreSQL + pgvector
worker    — Agent Worker（可选，MVP 可和 api 同一个进程）
```

**Redis 可以先没有** — 当出现多 Worker / 高频 streaming fanout / distributed locks / rate limiter 需求时再加入。

---

## 10. 最终 Repository 布局

```text
personal-ai-os/
├── apps/
│   ├── api/                    # FastAPI (Dept 12)
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── dependencies.py
│   │   ├── middleware/
│   │   ├── routers/
│   │   └── tests/
│   ├── web/                    # Next.js (Dept 12)
│   │   ├── app/
│   │   │   ├── chat/
│   │   │   ├── runs/
│   │   │   ├── memories/
│   │   │   ├── tools/
│   │   │   ├── automations/
│   │   │   └── security/
│   │   └── components/
│   └── cli/                    # CLI (Dept 12)
│       └── main.py
│
├── packages/
│   ├── agent_runtime/          # Dept 01
│   ├── gateway/                # Dept 02
│   ├── tool_broker/            # Dept 03
│   ├── policy_engine/          # Dept 04
│   ├── memory_engine/          # Dept 05
│   ├── context_engine/         # Dept 06
│   ├── skill_engine/           # Dept 07
│   ├── scheduler/              # Dept 08
│   ├── model_gateway/          # Dept 10
│   ├── observability/          # Dept 11
│   └── common/                 # 共享类型、工具函数
│       ├── models.py           # AgentState, DomainEvent, etc.
│       └── protocols.py        # 所有 Protocol 定义
│
├── connectors/                 # Dept 09
│   ├── filesystem/
│   ├── web_search/
│   ├── http_fetch/
│   ├── calculator/
│   ├── github/
│   ├── gmail/
│   ├── calendar/
│   ├── drive/
│   ├── browser/
│   └── shell/
│
├── mcp/                        # Dept 03
│   ├── client/
│   └── registry/
│
├── migrations/                 # Dept 12 (Alembic)
│   ├── versions/
│   └── alembic.ini
│
├── skills/                     # Dept 07
│   └── builtin/
│
├── evals/                      # Dept 11
│   └── datasets/
│
├── tests/                      # 集成测试 & E2E
│   ├── integration/
│   └── e2e/
│
├── deploy/
│   ├── docker/
│   │   ├── Dockerfile.api
│   │   └── Dockerfile.web
│   └── compose/
│       └── docker-compose.yml
│
├── docs/
│   ├── dept/                   # 本套 Department 工作指南
│   └── adr/                    # Architecture Decision Records
│
├── pyproject.toml
└── README.md
```

---

## 11. Python 包边界（强制约束）

```text
agent_runtime
  只能依赖: context, memory (interface), tool (interface),
            model (interface), policy (interface)
  不能直接: import gmail, import github, import browserbase
```

本 Department 通过 `packages/common/protocols.py` 定义所有接口，确保各模块遵循依赖倒置原则。

---

## 12. 详细任务列表

### Task 1: 项目脚手架
- [ ] Monorepo 初始化（pyproject.toml / package.json）
- [ ] `packages/common/` — 共享类型和 Protocol 定义
- [ ] 确保所有 Department 的 import 满足包边界约束

### Task 2: 数据库 Migration
- [ ] Alembic 初始化
- [ ] 所有核心表的 initial migration
- [ ] Migration 测试（upgrade/downgrade）

### Task 3: FastAPI 应用
- [ ] `main.py` — app 创建、CORS、middleware
- [ ] `dependencies.py` — DB session, service injection
- [ ] 所有 Router 实现（Chat/Sessions, Runs, Memories, Tools, Approvals, Automations, Gateway）

### Task 4: SSE Streaming
- [ ] SSE 端点实现
- [ ] Event generator（桥接 Agent Runtime 的 stream 到 SSE）
- [ ] Connection 管理（客户端断开时清理）

### Task 5: 认证
- [ ] API Key 认证（开发阶段）
- [ ] 请求级别 owner_id 注入
- [ ] session/memory 等资源的 owner_id 过滤

### Task 6: Next.js 前端
- [ ] Next.js App Router 项目创建
- [ ] 6 个页面实现（Chat, Runs, Memories, Tools, Automations, Security）
- [ ] SSE 客户端（EventSource 连接 + React state 管理）
- [ ] 组件库（Chat bubble, Tool call card, Approval card, Timeline）

### Task 7: CLI
- [ ] CLI 入口（typer/click）
- [ ] 交互式 Chat 模式
- [ ] 单条命令模式
- [ ] 配置管理（API URL, API Key）

### Task 8: Docker
- [ ] Dockerfile.api
- [ ] Dockerfile.web
- [ ] docker-compose.yml
- [ ] 开发环境一键启动脚本

### Task 9: 端到端测试
- [ ] `POST /v1/sessions/{id}/messages → SSE stream → run.completed` 完整链路
- [ ] 重启 API 后 session 持久化测试
- [ ] 前端 E2E（Cypress/Playwright）— 至少覆盖 Chat 页面基本流程

---

## 13. 验收标准

### API 端到端测试
```bash
# 1. 创建 Session
curl -X POST http://localhost:8000/v1/sessions \
  -H "X-API-Key: test_key" \
  -d '{"project_id": null}'
# → {"id": "sess_xxx", "status": "active"}

# 2. 发送消息
curl -X POST http://localhost:8000/v1/sessions/sess_xxx/messages \
  -H "X-API-Key: test_key" \
  -d '{"text": "你好"}'
# → {"run_id": "run_xxx", "status": "intake"}

# 3. 监听 SSE 流
curl -N http://localhost:8000/v1/runs/run_xxx/stream \
  -H "X-API-Key: test_key"
# → event:run.started → event:text.delta* → event:run.completed
```

### 前端基本功能
```text
打开 http://localhost:3000
    ↓
可以看到 Chat 界面
    ↓
输入 "你好" 并发送
    ↓
Agent 实时流式回复（打字机效果）
    ↓
消息历史持久化（刷新页面后仍在）
```

### Docker 一键启动
```bash
docker compose -f deploy/compose/docker-compose.yml up
# → api:8000, web:3000, postgres:5432 全部可用
```

---

## 14. 参考蓝图章节

- Section 36-39: PostgreSQL 设计 & 核心表 Schema
- Section 41: API 设计
- Section 42: Streaming (SSE)
- Section 43: Frontend 页面
- Section 44: Repository Layout
- Section 45: Python Package Boundary
- Section 46-47: Python Interfaces
- Section 58-60: Deployment
- Section 80: Authentication
- Section 93-97: 开发策略 & 代码起点
