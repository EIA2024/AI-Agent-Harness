# Department 02 — Gateway & Session

> **职责**：所有用户入口的统一接入层 — 将不同 Channel（Web/CLI/Telegram/Discord/Slack）的消息标准化为统一 `InboundMessage`，解析身份，路由到正确的 Session 和 Agent。
> **优先级**：P0（Foundation）
> **预估规模**：M
> **负责目录**：`packages/gateway/`

---

## 1. 部门边界

### 你负责
- 多渠道消息标准化（Channel Adapter）
- 统一消息结构 `InboundMessage`
- 身份解析（Identity Resolution）
- Session Router（channel + sender + conversation → session_id）
- Session 数据模型与生命周期管理
- Session 上下文保存（当前 Project、Agent、Tool state）

### 你不负责
- Agent 的实际执行 → Department 01
- 消息内容理解 → Department 01（通过 Model）
- 用户认证（WebAuthn/OAuth）→ Department 12
- 渠道底层实现（Telegram Bot 搭建等）→ 你可以实现 Telegram/Discord 等 adapter 的基础框架，具体 bot token 配置由 Infrastructure 负责

### 你的上游依赖
- Agent Runtime（Department 01）— 将标准化消息投递给 Runtime
- API Layer（Department 12）— 提供 Web hook 端点

### 你提供给下游
- 标准化 `InboundMessage`
- `session_id` 解析
- `Session` 对象（含 project_id, agent_id, 历史引用）

---

## 2. 统一消息结构

所有 Channel 的消息必须转成这个结构后才能进入系统：

```python
from pydantic import BaseModel
from uuid import UUID
from datetime import datetime

class Attachment(BaseModel):
    type: str  # "image", "file", "link", "voice"
    url: str | None
    data: bytes | None  # 仅小文件，大文件用 url
    mime_type: str | None
    filename: str | None

class InboundMessage(BaseModel):
    event_id: UUID

    # Channel info
    channel: str  # "web" | "cli" | "telegram" | "discord" | "slack" | "api"
    channel_account_id: str  # 哪个 bot 账号收到的

    # Sender
    sender_id: str  # Channel 内的用户标识（如 Telegram user_id）
    owner_id: UUID  # Personal AI OS 内部的用户 UUID

    # Conversation routing
    conversation_key: str  # 同一对话的标识（DM: sender_id, Group: chat_id+thread_id）

    # Content
    text: str | None
    attachments: list[Attachment]

    # Threading
    reply_to: str | None  # 回复哪条消息

    # Metadata
    timestamp: datetime
    metadata: dict  # Channel 特有字段（如 Telegram message_id）
```

---

## 3. Channel Adapter 架构

每个 Channel 实现一个 Adapter：

```python
from typing import Protocol

class ChannelAdapter(Protocol):
    channel_name: str

    async def parse_inbound(self, raw_payload: dict) -> InboundMessage:
        """将 Channel 原始消息转为 InboundMessage"""
        ...

    async def send_outbound(self, session_id: UUID, text: str, attachments: list | None = None) -> None:
        """向 Channel 发送回复"""
        ...

    async def validate_webhook(self, raw_payload: dict) -> bool:
        """验证 webhook 签名（防止伪造消息）"""
        ...
```

### 第一批需要实现的 Adapter

| Channel | 优先级 | 说明 |
|---------|--------|------|
| Web | P0 | 通过 WebSocket/SSE 连接到 Web App |
| CLI | P0 | stdin/stdout 或本地 socket |
| Telegram | P1 | Bot API webhook |
| Discord | P2 | Bot webhook |
| Slack | P2 | Events API |

---

## 4. Session Router

### 核心逻辑

```text
channel + sender_id + conversation_key + agent_id(optional) + project_id(optional)
    ↓
Session Router
    ↓
session_id
```

### Session 数据模型

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    owner_id UUID NOT NULL REFERENCES users(id),
    agent_id UUID REFERENCES agents(id),

    -- Routing keys
    channel TEXT NOT NULL,
    external_conversation_id TEXT,  -- Telegram chat_id / Discord channel_id

    -- Context
    project_id UUID REFERENCES projects(id),
    title TEXT,  -- 自动生成的会话标题

    -- State
    status TEXT NOT NULL DEFAULT 'active',  -- active | paused | archived
    context JSONB NOT NULL DEFAULT '{}',    -- 当前工作上下文

    -- Active run tracking
    active_run_id UUID REFERENCES runs(id),

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sessions_routing
    ON sessions(owner_id, channel, external_conversation_id);
```

### Session 应保存的上下文

```python
class SessionContext(BaseModel):
    # 当前对话历史引用（不是完整历史，是指向 messages 表的引用）
    message_window: list[UUID]  # 最近 N 条 message id

    # 当前工作上下文
    current_project_id: UUID | None
    current_task: str | None  # 用户当前正在讨论的任务

    # Tool state（某些工具有跨轮次状态）
    tool_state: dict  # e.g. {"browser_session_id": "xxx", "shell_cwd": "/home/user"}

    # 临时内存（本轮有效，不持久化到 Memory）
    scratchpad: dict
```

---

## 5. Identity Resolution

### 流程

```text
Channel + sender_id
    ↓
Lookup external_identity 表
    ↓
找到 owner_id（Personal AI OS 内部用户）
    ↓
如果找不到 → 创建 external_identity → 关联到 owner
```

### External Identity 模型

```sql
CREATE TABLE external_identities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    owner_id UUID NOT NULL REFERENCES users(id),

    channel TEXT NOT NULL,
    external_user_id TEXT NOT NULL,  -- Telegram user_id, Discord user_id, etc.

    display_name TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(channel, external_user_id)
);
```

---

## 6. Gateway 请求处理流程

```text
1. Webhook 到达 /gateway/{channel}/webhook
    ↓
2. Channel Adapter 验证签名
    ↓
3. parse_inbound() → InboundMessage
    ↓
4. Identity Resolution → owner_id
    ↓
5. Session Router → session_id（新建或复用）
    ↓
6. 创建 Message 记录
    ↓
7. 投递给 Agent Runtime（创建 Run）
    ↓
8. 流式返回 Agent 回复给 Channel Adapter
```

---

## 7. 详细任务列表

### Task 1: 定义核心数据模型
- [ ] `InboundMessage` / `Attachment` Pydantic models
- [ ] `Session` SQLAlchemy model
- [ ] `ExternalIdentity` SQLAlchemy model
- [ ] `SessionContext` 结构

### Task 2: 实现 Channel Adapter 框架
- [ ] `ChannelAdapter` Protocol 定义
- [ ] Web Adapter（接收来自 Frontend 的消息）
- [ ] CLI Adapter（stdin/stdout）
- [ ] Telegram Adapter（Bot API）

### Task 3: 实现 Session Router
- [ ] Session 创建逻辑（首次消息）
- [ ] Session 复用逻辑（按 routing key 查找已有 session）
- [ ] Session 上下文读取/更新
- [ ] Session 归档（用户手动或自动过期）

### Task 4: 实现 Identity Resolution
- [ ] External Identity 的 CRUD
- [ ] 自动关联（同一 channel 同一 sender → 同一 owner）
- [ ] 多 Channel 同一 owner 的合并

### Task 5: 实现 Gateway Webhook 端点
- [ ] `POST /gateway/{channel}/webhook` 统一端点
- [ ] 各 Channel 的签名验证
- [ ] 请求验证：拒绝非白名单 channel 的消息

### Task 6: 端到端集成
- [ ] Web → Gateway → Session → Runtime → 回复 完整链路
- [ ] CLI → Gateway → Session → Runtime → 回复 完整链路

---

## 8. 验收标准

### 多渠道消息标准化
```text
Telegram 消息 "你好"（JSON payload）
    ↓ Webhook
Gateway Telegram Adapter
    ↓
InboundMessage {channel: "telegram", sender_id: "12345", text: "你好", ...}
```

### Session 路由
```text
同一 Telegram 用户发送两条消息
    ↓
进入同一个 session_id
    ↓
Agent 能看到上一条消息的上下文
```

### 跨 Session 隔离
```text
Telegram 用户 A → session_1
Telegram 用户 B → session_2
    ↓
session_1 的 context 不会 leak 到 session_2
```

---

## 9. 参考蓝图章节

- Section 5: Gateway 架构
- Section 6: Session 设计
- Section 44: Repository Layout（gateway 部分）
- Section 80: Authentication（与 Gateway 的身份解析配合）
