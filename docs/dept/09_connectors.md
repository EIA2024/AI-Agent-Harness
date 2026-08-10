# Department 09 — Connectors

> **职责**：实现所有原生连接器（Native Connectors），为 Agent 提供与外部世界交互的能力。每个 Connector 实现统一的 `Connector` Protocol，通过 Tool Broker 被 Agent 调用。原则：先 Read，后 Write。
> **优先级**：P1（Core Capability）
> **预估规模**：L
> **负责目录**：`connectors/`

---

## 1. 部门边界

### 你负责
- `Connector` Protocol 的实现（每个外部服务一个 Connector）
- OAuth 认证流程（每个 Connector 维护自己的 connection/token）
- 工具的参数校验、执行、结果格式化
- 每个 Connector 的 ToolDescriptor 注册
- 错误处理与重试（Connector 级别的错误）
- Browser Session 管理（隔离的浏览器会话）

### 你不负责
- Tool Broker 的路由调度 → Department 03
- Policy/Approval 判断 → Department 04
- Credential 存储 → Department 04（你通过 Credential Broker 获取 token）
- MCP Server 连接 → Department 03（MCP Manager）

### 你的上游依赖
- Tool System（Department 03）— `Connector` Protocol 定义
- Security（Department 04）— Credential Broker / Sandbox
- API（Department 12）— OAuth callback 端点

### 你提供给下游
- `Connector.list_tools()` → Department 03（Tool Registry 注册）
- `Connector.execute(tool, arguments, ctx)` → Department 03（Tool Broker 调用）

---

## 2. Connector Protocol（必须实现）

```python
from typing import Protocol

class ToolExecutionContext(BaseModel):
    run_id: UUID
    session_id: UUID
    owner_id: UUID
    agent_id: UUID
    idempotency_key: str
    approved_by: UUID | None
    approval_id: UUID | None

class ToolResult(BaseModel):
    success: bool
    data: dict | None       # 结构化结果
    text: str | None         # 人类可读结果（用于 Agent context）
    error: str | None
    error_code: str | None
    latency_ms: int
    truncated: bool
    raw_size_bytes: int      # 原始结果大小（截断前）

class Connector(Protocol):
    connector_name: str

    async def list_tools(self) -> list[ToolDescriptor]:
        """返回此 Connector 提供的所有工具"""
        ...

    async def execute(
        self,
        tool: str,            # e.g. "list_issues"（不含 namespace prefix）
        arguments: dict,
        ctx: ToolExecutionContext,
    ) -> ToolResult:
        """执行具体的工具调用"""
        ...
```

---

## 3. 第一批 Connectors（按优先级排序）

| # | Connector | 目录 | 优先级 | Read-first? |
|---|-----------|------|--------|-------------|
| 1 | Filesystem | `connectors/filesystem/` | P0 | Read first |
| 2 | Web Search | `connectors/web_search/` | P0 | Read only |
| 3 | HTTP Fetch | `connectors/http_fetch/` | P0 | Read only |
| 4 | Calculator | `connectors/calculator/` | P0 | N/A |
| 5 | GitHub | `connectors/github/` | P1 | Read first |
| 6 | Google Calendar | `connectors/calendar/` | P1 | Read first |
| 7 | Gmail | `connectors/gmail/` | P1 | Read first |
| 8 | Google Drive | `connectors/drive/` | P2 | Read first |
| 9 | Browser | `connectors/browser/` | P2 | Read first |
| 10 | Shell | `connectors/shell/` | P2 | Sandbox |

### 每个 Connector 的开放顺序

```text
Step 1: 只开放 Read 工具
Step 2: 测试 + 用户确认
Step 3: 开放 Draft/Create（不直接生效）
Step 4: 测试 + 用户确认
Step 5: 开放 Send/Push/Delete（需要 Approval）
```

---

## 4. Filesystem Connector

最基础的 Connector，也是第一个要实现的（用于验证整个 Tool System 链路）。

### 工具列表

```yaml
tools:
  - name: filesystem.read
    risk: R1
    description: "Read a file from the local filesystem"
    arguments: {path: string, encoding: string = "utf-8", max_lines: int = 500}

  - name: filesystem.write
    risk: R2
    description: "Write content to a file"
    arguments: {path: string, content: string, mode: "overwrite" | "append"}

  - name: filesystem.list
    risk: R1
    description: "List files in a directory"
    arguments: {path: string, recursive: bool = false, max_depth: int = 3}

  - name: filesystem.search
    risk: R1
    description: "Search file contents using grep-like patterns"
    arguments: {path: string, pattern: string, file_pattern: string = "*", max_results: int = 50}
```

### 安全约束
- 所有路径必须在一个白名单的根目录内（`allowed_root`）
- 禁止读取 `.env`, `*.pem`, `*id_rsa*`, `*credentials*` 等文件
- `write` 不覆盖已有文件（需要 Approval）

---

## 5. GitHub Connector

### 工具列表（Read — Phase 1）

```yaml
tools:
  - name: github.search_repositories
    risk: R1
  - name: github.get_repository
    risk: R1
  - name: github.list_commits
    risk: R1
  - name: github.list_pull_requests
    risk: R1
  - name: github.list_issues
    risk: R1
  - name: github.get_issue
    risk: R1
  - name: github.search_code
    risk: R1
  - name: github.get_file_contents
    risk: R1
```

### 工具列表（Write — Phase 2+）

```yaml
tools:
  - name: github.create_issue
    risk: R3
    approval: required
  - name: github.create_pull_request
    risk: R3
    approval: required
  - name: github.create_comment
    risk: R3
    approval: required
  - name: github.merge_pull_request
    risk: R4
    approval: strict
```

### OAuth 流程

```text
1. 用户在 Connections 页面点击 "Connect GitHub"
2. OAuth 2.0 flow → 获取 access_token
3. 存储到 Credential Broker（secret_ref）
4. 用户配置 grant scope（repo read / issues write / ...）
5. Connector 使用 token 调用 GitHub API
```

---

## 6. Gmail Connector

### 安全策略（格外重要 — 邮件是高风险外部通信）

```text
Phase 1（Read）:
  ✓ gmail.search_emails
  ✓ gmail.get_email
  ✓ gmail.list_labels

Phase 2（Draft — 不实际发送）:
  ✓ gmail.create_draft

Phase 3（Send — 需要 Approval）:
  ✓ gmail.send_email (R3: to self → R2, to external → R3)

Phase 4（高风险）:
  ✓ gmail.delete_email (R4: strict approval)
  ✓ gmail.modify_filters (R4)
```

### 收件人风险判断

```python
# 在 Policy Engine 中配置 Gmail 特定规则
async def gmail_risk_adjustment(arguments, owner_profile):
    to = arguments.get("to", "")
    if to == owner_profile.email or to.endswith(f"@{owner_profile.domain}"):
        return "R2"  # 内部通信
    else:
        return "R3"  # 外部通信 — 需要 Approval
```

---

## 7. Browser Connector

### 优先级策略

```
API call       ← 最高优先级（如 GitHub API）
    ↓
HTTP fetch     ← 简单页面
    ↓
DOM / a11y tree ← 需要交互但不复杂的页面
    ↓
Browser automation ← 需要点击、表单填写的复杂场景
    ↓
Vision computer-use ← 最后手段（慢、贵、不稳定）
```

**不要所有网页任务都直接截图 + Vision。**

### Browser Session 管理

```sql
CREATE TABLE browser_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    owner_id UUID NOT NULL REFERENCES users(id),
    profile_id UUID,  -- 独立的浏览器 profile（隔离）

    cookies_ref TEXT,        -- 加密存储的 cookies
    domain_allowlist JSONB,  -- 允许访问的域名列表

    status TEXT NOT NULL DEFAULT 'active',
    expires_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**不要直接把用户日常 Chrome Profile 暴露给 Agent。**

### 工具示例

```yaml
tools:
  - name: browser.navigate
    risk: R1
  - name: browser.get_content
    risk: R1  # DOM text / accessibility tree
  - name: browser.click
    risk: R2  # 可能触发 unintended action
  - name: browser.fill_form
    risk: R3  # 输入内容可能造成副作用
  - name: browser.screenshot
    risk: R1  # vision-based understanding
```

---

## 8. Shell Connector

所有 shell 执行进入 Sandbox（Department 04 管理）。

### 工具

```yaml
tools:
  - name: shell.execute
    risk: R2  # Default R2，但 Policy 会提升为 ask
    sandbox: required
    network: deny  # 默认无网络
    timeout: 60s

  - name: shell.python
    risk: R2
    sandbox: required
    network: restricted  # PyPI only
    timeout: 120s
```

---

## 9. OAuth Connection 管理

```sql
CREATE TABLE connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    owner_id UUID NOT NULL REFERENCES users(id),

    provider TEXT NOT NULL,   -- "github" | "google" | "microsoft"
    scopes JSONB NOT NULL,    -- ["repo.read", "issues.write"]

    token_ref TEXT NOT NULL,  -- 指向 credentials_refs

    status TEXT NOT NULL DEFAULT 'active',  -- active | expired | revoked

    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 最小权限原则

```text
GitHub:
  第一阶段: repo:read, issues:read
  用户启用 Write 后: repo:write, issues:write

Gmail:
  第一阶段: gmail.readonly
  用户启用 Send 后: gmail.send
  绝不一上来申请 gmail.modify 全权限
```

---

## 10. 详细任务列表

### Task 1: 实现 Connector 框架
- [ ] `Connector` Protocol 最终版（与 Department 03 对齐）
- [ ] `ToolResult` 标准格式
- [ ] Connector 注册/加载机制
- [ ] 统一的错误处理装饰器

### Task 2: 实现 Filesystem Connector
- [ ] `filesystem.read` / `filesystem.write` / `filesystem.list` / `filesystem.search`
- [ ] 路径安全检查（白名单根目录）
- [ ] 敏感文件保护（禁止读取 .env, id_rsa 等）

### Task 3: 实现 Web Search & HTTP Fetch
- [ ] `web.search` — 集成搜索 API
- [ ] `http.fetch` — HTTP GET/POST with timeout
- [ ] HTML → text/markdown 转换

### Task 4: 实现 Calculator
- [ ] 安全的数学表达式求值
- [ ] 限制度：不允许 import、不允许 file access

### Task 5: 实现 GitHub Connector
- [ ] OAuth flow
- [ ] Read 工具（search_repos, list_commits, list_prs, list_issues, get_file）
- [ ] Write 工具（create_issue, create_comment — 需 Approval）
- [ ] Rate limit 处理

### Task 6: 实现 Google Calendar Connector
- [ ] OAuth flow
- [ ] `calendar.list_events` / `calendar.get_event`
- [ ] `calendar.create_event` (R3, Approval)
- [ ] Timezone 处理

### Task 7: 实现 Gmail Connector
- [ ] OAuth flow（最小 scope: gmail.readonly）
- [ ] `gmail.search_emails` / `gmail.get_email`
- [ ] `gmail.create_draft` (R2)
- [ ] `gmail.send_email` (R3, Approval + 收件人风险判断)
- [ ] 绝不暴露 raw email headers/secrets

### Task 8: 实现 Browser Connector
- [ ] Browser Session 管理
- [ ] `browser.navigate` / `browser.get_content`
- [ ] `browser.click` / `browser.fill_form`
- [ ] `browser.screenshot`

### Task 9: 实现 Shell Connector
- [ ] `shell.execute`（通过 Sandbox）
- [ ] 默认 deny network
- [ ] 危险命令检测（rm -rf, curl to unknown host, etc.）

### Task 10: 测试
- [ ] 每个 Connector 的 `list_tools()` 返回正确 ToolDescriptor
- [ ] 每个 Connector 的 `execute()` 正确执行
- [ ] Read-only 工具确认无副作用
- [ ] OAuth 流程测试
- [ ] Error handling 测试（auth expired, rate limit, timeout）

---

## 11. 验收标准

### Connector 正确注册
```text
Filesystem Connector 启动
    ↓
ToolRegistry.register(list_tools())
    ↓
ToolRegistry.list_all() 包含 filesystem.* 工具
    ↓
Agent 可以通过 ToolBroker 调用 filesystem.read
```

### Read-first 策略验证
```text
GitHub Connector Phase 1:
  ✓ list_commits, search_repos （可用）
  ✗ create_issue, delete_repo （未注册）

Gmail Connector Phase 1:
  ✓ gmail.search_emails, gmail.get_email （可用）
  ✗ gmail.send_email （未注册）
```

---

## 12. 参考蓝图章节

- Section 24: Native Connectors（第一批列表）
- Section 25: Connector Contract
- Section 22: Browser Agent
- Section 23: Credentials
- Section 81: External OAuth
- Section 82: Least Privilege
