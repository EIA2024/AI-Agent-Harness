# Department 08 — Scheduler & Autonomy

> **职责**：使 Personal AI OS 能在用户输入之外主动运行。涵盖定时任务（Cron）、延迟任务（Delayed）、条件监控（Condition Watch）、内部事件总线（Event Bus）、Artifact 管理和 Project Context。
> **优先级**：P2（Autonomy）
> **预估规模**：M
> **负责目录**：`packages/scheduler/`

---

## 1. 部门边界

### 你负责
- **Trigger Engine**：Cron / Delayed / Webhook / Poller / Internal Event
- **Automation 数据模型**与生命周期
- **Run Queue**：将触发事件转为 Agent Run
- **Event Bus**：内部统一事件系统
- **Condition Watcher**：定期轮询检查条件（如 "GitHub issue 被回复时通知我"）
- **Artifact 系统**：Agent 产出的文件（report、code、PDF 等）
- **Project Context**：Project 级别的记忆、文件、Session 隔离

### 你不负责
- Agent Run 的执行 → Department 01
- 通知推送（Telegram/Discord 等）→ Department 02（Gateway）
- 实际的条件检查逻辑（如调用 GitHub API）→ Department 09
- 记忆的 project scope 管理 → Department 05

### 你的上游依赖
- Agent Runtime（Department 01）— 创建 Run
- Gateway（Department 02）— 推送通知
- Connectors（Department 09）— Condition Watch 需要调用工具

### 你提供给下游
- `EventBus.publish(event)` — 所有模块 emit 事件
- `EventBus.subscribe(event_type, handler)` — 所有模块订阅事件
- `Scheduler.create_automation(...)` — 来自用户或 Agent
- `RunQueue.enqueue(run_config)` — 创建后台 Run

---

## 2. Trigger Architecture

```
Cron ─────────┐
Delayed ──────┤
Webhook ──────┼──→ Trigger Engine ──→ Run Queue ──→ Agent Runtime
Poller ───────┤
Internal Event┘
```

### 2.1 Scheduled（Cron）

"每天早上 8:00 给我发送今日摘要"

```python
cron_trigger = {
    "type": "cron",
    "expression": "0 8 * * *",  # 每天 08:00 本地时间
    "timezone": "Asia/Shanghai"
}
```

### 2.2 Delayed

"两小时后提醒我"

```python
delayed_trigger = {
    "type": "delayed",
    "fire_at": "2026-08-10T16:00:00+08:00"
}
```

### 2.3 Webhook

"当 GitHub webhook 到达时执行"

```python
webhook_trigger = {
    "type": "webhook",
    "source": "github",
    "events": ["push", "pull_request.opened"],
    "endpoint": "/webhooks/github"
}
```

### 2.4 Poller（Condition Watch）

"当 GitHub issue #123 被回复时通知我"

```python
poller_trigger = {
    "type": "poller",
    "check_interval_seconds": 300,  # 每 5 分钟检查一次
    "condition_tool": "github.list_issue_comments",
    "condition_args": {"owner": "user", "repo": "project", "issue_number": 123},
    "condition_check": "new_comments_since_last_check > 0"
}
```

### 2.5 Internal Event

Agent 内部事件触发（如 "run completed" → 触发 Reflection）

```python
event_trigger = {
    "type": "event",
    "event_type": "run.completed",
    "filter": {"owner_id": "..."}
}
```

---

## 3. Automation 数据模型

```sql
CREATE TABLE automations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    owner_id UUID NOT NULL REFERENCES users(id),
    agent_id UUID,  -- 哪个 Agent 创建的

    name TEXT NOT NULL,  -- 用户可读名称
    description TEXT,

    -- 触发配置
    trigger_type TEXT NOT NULL,  -- cron | delayed | webhook | poller | event
    trigger_config JSONB NOT NULL,  -- 具体配置

    -- 执行内容
    prompt TEXT NOT NULL,  -- 发给 Agent 的 prompt（或 Skill 引用）
    skill_id UUID REFERENCES skills(id),  -- 可选：引用已有 Skill

    -- 上下文策略
    context_policy JSONB NOT NULL DEFAULT '{}',
    -- e.g. {"project_id": "...", "include_recent_conversation": false,
    --       "max_memories": 5, "allowed_tools": ["github.*", "web.search"]}

    -- 通知
    notify_channel TEXT,  -- 结果推送到哪个 channel
    notify_config JSONB,  -- e.g. {"telegram_chat_id": "..."}

    -- 状态
    status TEXT NOT NULL DEFAULT 'active',  -- active | paused | error | completed
    enabled BOOLEAN NOT NULL DEFAULT true,

    -- 运行追踪
    last_run_at TIMESTAMPTZ,
    last_run_status TEXT,  -- success | failed
    next_run_at TIMESTAMPTZ,

    -- 时间戳
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Automation 运行历史
CREATE TABLE automation_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    automation_id UUID NOT NULL REFERENCES automations(id),

    run_id UUID NOT NULL REFERENCES runs(id),  -- 对应的 Agent Run

    trigger_type TEXT NOT NULL,
    trigger_payload JSONB,

    status TEXT NOT NULL,  -- running | success | failed | cancelled

    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,

    error_message TEXT
);
```

---

## 4. Event Bus

内部所有模块通过 Event Bus 通信，不直接 import 彼此。

### 统一事件结构

```python
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

class DomainEvent(BaseModel):
    id: UUID
    type: str  # 事件类型
    timestamp: datetime

    owner_id: UUID
    run_id: UUID | None
    session_id: UUID | None

    payload: dict  # 事件特定数据
```

### 核心事件类型

```text
# Message
message.received     — Gateway 收到新消息

# Run
run.started          — Agent Run 开始
run.completed        — Agent Run 成功完成
run.failed           — Agent Run 失败
run.cancelled        — Agent Run 被取消

# Tool
tool.requested       — Agent 请求调用工具
tool.started         — 工具开始执行
tool.completed       — 工具执行完成
tool.failed          — 工具执行失败
tool.denied          — 工具被 Policy 拒绝

# Approval
approval.created     — 创建审批请求
approval.approved    — 审批通过
approval.rejected    — 审批拒绝

# Memory
memory.created       — 新记忆创建
memory.updated       — 记忆更新
memory.deleted       — 记忆删除（forget）

# Automation
automation.triggered  — Automation 被触发
automation.completed  — Automation Run 完成
automation.failed     — Automation Run 失败
```

### Event Bus 实现

```python
import asyncio
from collections import defaultdict

class EventBus:
    def __init__(self):
        self._handlers: dict[str, list[callable]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: callable) -> None:
        """订阅事件。handler 是 async callable，接收 DomainEvent。"""
        self._handlers[event_type].append(handler)

    async def publish(self, event: DomainEvent) -> None:
        """发布事件，所有匹配的 handler 异步并发执行。"""
        handlers = self._handlers.get(event.type, [])
        tasks = [handler(event) for handler in handlers]
        await asyncio.gather(*tasks, return_exceptions=True)  # 单个 handler 失败不影响其他
```

**关键约束**：Event handlers 不应长时间阻塞。耗时操作应异步化。

---

## 5. Artifact System

Agent 产出的文件不直接塞数据库 JSON blob。

```sql
CREATE TABLE artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    owner_id UUID NOT NULL REFERENCES users(id),
    run_id UUID NOT NULL REFERENCES runs(id),
    session_id UUID REFERENCES sessions(id),

    name TEXT NOT NULL,         -- 文件名
    type TEXT NOT NULL,         -- report | code | image | pdf | dataset
    mime_type TEXT NOT NULL,

    size_bytes BIGINT NOT NULL,
    storage_path TEXT NOT NULL, -- 本地文件系统路径 | 未来 S3 key

    metadata JSONB NOT NULL DEFAULT '{}',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**MVP**：本地文件系统存储（`data/artifacts/{owner_id}/{artifact_id}/{filename}`）
**未来**：S3-compatible object storage

---

## 6. Project Context

Personal AI OS 不能只有 global memory，需要 Project 级别的隔离。

```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    owner_id UUID NOT NULL REFERENCES users(id),

    name TEXT NOT NULL,
    description TEXT,

    -- Project 级别的配置
    config JSONB NOT NULL DEFAULT '{}',
    -- e.g. {"default_skills": ["code_review"], "repo_urls": ["..."],
    --       "notify_on_activity": true}

    status TEXT NOT NULL DEFAULT 'active',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Project 拥有：
- Files（项目文件索引）
- Memories（scope = `project:<id>`）
- Skills（项目特定 Skill）
- Sessions（`project_id` 关联）
- Automations（项目级定时任务）
- Connections（项目级 Connector 配置）

### Context Scope

每次 query 的 Context 应该：
```text
global scope
+
current project scope
+
current session scope
```

不要让 Project A 的 Memory 大量进入 Project B 的 context（通过 scope filter 控制）。

---

## 7. Scheduler 核心接口

```python
class Scheduler:
    def __init__(
        self,
        event_bus: EventBus,
        agent_runtime,  # Runtime interface (Dept 01)
    ):
        ...

    async def create_automation(
        self,
        name: str,
        trigger_type: str,
        trigger_config: dict,
        prompt: str,
        owner_id: UUID,
        notify_channel: str | None = None,
    ) -> Automation:
        """创建 Automation（来自用户指令或 Agent 行为）"""
        ...

    async def start(self) -> None:
        """启动 Scheduler 主循环（加载所有 active automation，开始监听）"""
        ...

    async def stop(self) -> None:
        """停止 Scheduler（优雅关闭）"""
        ...

    async def trigger_now(self, automation_id: UUID) -> None:
        """手动触发一次 Automation"""
        ...

    async def update_automation(self, automation_id: UUID, updates: dict) -> Automation:
        """更新 Automation 配置"""
        ...

    async def delete_automation(self, automation_id: UUID) -> None:
        """删除 Automation"""
        ...
```

---

## 8. 第一阶段不要做

- ❌ Temporal（等出现 multi-worker / webhook waiting / workflow versioning 需求时引入）
- ❌ 复杂的分布式 Run Queue（第一版单 Worker 足够）
- ❌ Redis Pub/Sub（如果事件必须 replay，未来用 Redis Streams）
- ❌ S3 object storage（先本地文件系统）

**推荐边界**：LangGraph Checkpointer（PostgreSQL）+ 简单的 asyncio-based scheduler 即可覆盖 MVP 的 Automation 需求。

---

## 9. 详细任务列表

### Task 1: 实现 Event Bus
- [ ] `DomainEvent` 模型
- [ ] `EventBus` 类（subscribe / publish）
- [ ] 所有核心事件类型定义
- [ ] Event handler 错误隔离（一个 handler 失败不影响其他）

### Task 2: 实现 Trigger Engine
- [ ] Cron parser（5-field cron → next fire time）
- [ ] Delayed task scheduler（one-shot fire at）
- [ ] Webhook receiver（`POST /webhooks/{source}`）
- [ ] Poller loop（按 interval 运行 check）
- [ ] Internal event subscriber

### Task 3: 实现 Automation 管理
- [ ] `automations` CRUD
- [ ] `automation_runs` 历史记录
- [ ] Automation 状态机（active / paused / error / completed）
- [ ] 服务重启后恢复所有 active automation
- [ ] 执行上限（max consecutive failures → status=error）

### Task 4: 实现 Run Queue
- [ ] 从 Trigger Engine 接收触发事件
- [ ] 创建 Agent Run（调用 Department 01）
- [ ] 结果通知（推送到 notify_channel）

### Task 5: 实现 Artifact System
- [ ] `artifacts` 表 + migration
- [ ] 本地文件存储
- [ ] Artifact CRUD API

### Task 6: 实现 Project Context
- [ ] `projects` 表 + migration
- [ ] Project CRUD
- [ ] Scope filter 集成（Memory/Session 按 project 隔离）

### Task 7: 测试
- [ ] Cron trigger 正确触发测试
- [ ] Delayed task 延迟执行测试
- [ ] 服务重启后 Automation 恢复测试
- [ ] Poller condition check 测试
- [ ] Event Bus 错误隔离测试（一个 handler 抛异常，其他正常执行）

---

## 10. 验收标准

### Autonomy DoD
```text
用户: "每天早上总结我的日历"
    ↓
Agent 创建 Automation Draft
    ↓
用户确认
    ↓
Scheduler 持久保存
    ↓
重启服务
    ↓
Automation 仍存在且 next_run_at 正确
    ↓
到时触发 → 创建 Run → 执行 → 推送结果到 Channel
```

### Event Bus 集成测试
```text
Agent Runtime publish "tool.completed"
    ↓
Observability 模块收到事件 → 记录 metric
Memory 模块收到事件 → 触发 async extraction
    ↓
两个 handler 独立执行，一个失败不影响另一个
```

---

## 11. 参考蓝图章节

- Section 28: Scheduler / Proactive Agent
- Section 29: Task 数据模型
- Section 30: Trigger Architecture
- Section 31: Event Bus
- Section 32: Durable Execution（Temporal 引入时机）
- Section 75: Artifact System
- Section 76: Project Context
- Section 77: Context Scope
- Section 89: Autonomy Definition of Done
