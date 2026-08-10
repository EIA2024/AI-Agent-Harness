# Department 07 — Skills System

> **职责**：管理可检索的流程知识（"How to do something"）。Skill 是 Procedural Memory 的成熟形态 — 可版本化、可触发、可评估、可由 Agent 自动生成（需审批）。
> **优先级**：P2（Autonomy）
> **预估规模**：M
> **负责目录**：`packages/skill_engine/`

---

## 1. 部门边界

### 你负责
- Skill Manifest 定义与解析
- Skill 注册、检索、上下文注入
- Skill 生命周期管理（Install → Validate → Trust Review → Enable → Use → Evaluate → Update）
- Agent 自动生成的 Skill Draft（Generated → Draft → User Approve → Active）
- Skill 版本管理
- Skill 触发条件匹配（当用户 query 匹配 trigger 时自动加载）
- Reflection 结果中的 Skill Candidate 消费

### 你不负责
- Procedural Memory（Skill 的未成熟形态）→ Department 05
- Reflection 的执行时机 → Department 01（Runtime Reflect 节点）
- 如何在 Prompt 中使用 Skill → Department 06（Context Engine 调用你）
- Skill 的安全性审查（Trust Review 阶段）→ Department 04

### 你的上游依赖
- Memory System（Department 05）— 某些 Skill 从 Procedural Memory 孵化
- Security（Department 04）— Trust Review 检查
- Event Bus（Department 08）— 接收 Reflection 事件

### 你提供给下游
- `SkillEngine.search(query)` → Department 06（Context Engine 注入相关 Skill）
- `SkillEngine.create_draft(candidate)` → Department 01（Reflection 后自动创建）

---

## 2. Skill Manifest

```yaml
# skills/builtin/github_weekly_report.yaml
id: github_weekly_report
version: 1.2.0

name: "GitHub Weekly Report"
description: >
  Generate a weekly development report from GitHub activity.
  Searches commits and PRs across the user's repos, groups work by project,
  and produces a concise markdown report.

triggers:
  - weekly report
  - summarize github work
  - what did i do this week
  - 周报
  - 本周工作总结

required_tools:
  - github.search_commits
  - github.list_pull_requests
  - github.get_repository

optional_tools:
  - github.list_issues

risk:
  level: 1  # R1: read-only

estimated_duration: "2-5 minutes"

instructions: |
  ## Goal
  Generate a weekly development report from GitHub activity.

  ## Steps
  1. Resolve the reporting date range. If the user doesn't specify, default to the last 7 days.
  2. Use `github.search_commits` to find all commits by the user in the date range.
  3. Use `github.list_pull_requests` to find all PRs the user created or merged.
  4. Group all activity by repository.
  5. For each repository, summarize:
     - Number of commits
     - PRs created / merged
     - Key themes from commit messages
  6. Produce a concise markdown report with:
     - Header: "Weekly Report: YYYY-MM-DD to YYYY-MM-DD"
     - Per-repo sections
     - Overall summary

  ## Output Format
  ```markdown
  # Weekly Report: {start_date} to {end_date}

  ## {repo_name}
  - {N} commits: {summary}
  - PRs: {list}
  ...
  ```

examples:
  - input: "Generate my weekly report"
    expected_tools: [github.search_commits, github.list_pull_requests]
```

### 对应的数据模型

```sql
CREATE TABLE skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES users(id),

    skill_id TEXT NOT NULL,  -- unique slug: e.g. "github_weekly_report"
    name TEXT NOT NULL,
    description TEXT NOT NULL,

    current_version TEXT NOT NULL,  -- 当前生效版本

    status TEXT NOT NULL DEFAULT 'draft',
    -- draft | review | active | disabled | deprecated

    source TEXT NOT NULL DEFAULT 'builtin',
    -- builtin | user_created | agent_generated | community

    risk_level INTEGER NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(owner_id, skill_id)
);

CREATE TABLE skill_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id UUID NOT NULL REFERENCES skills(id),

    version TEXT NOT NULL,  -- semver: 1.0.0
    manifest JSONB NOT NULL,  -- 完整的 YAML 转 JSON 存储

    changelog TEXT,

    created_by TEXT NOT NULL,  -- "user" | "agent:<agent_id>"
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(skill_id, version)
);
```

---

## 3. Skill Engine 核心接口

```python
class SkillEngine:
    def __init__(
        self,
        db_session,  # SQLAlchemy async session
        tool_registry,  # ToolRegistry (Dept 03) — 验证 required_tools 是否可用
    ):
        ...

    async def search(
        self,
        query: str,
        owner_id: UUID,
        limit: int = 5,
    ) -> list[Skill]:
        """
        搜索匹配的 Skill：
        1. 查询所有 active skill
        2. 按 trigger keywords 匹配 query
        3. 按 description embedding 语义匹配
        4. 过滤：required_tools 必须全部可用
        5. 返回 top K Skill（含当前版本的完整 manifest）
        """
        ...

    async def get(self, skill_id: str, owner_id: UUID) -> Skill | None:
        """按 skill_id slug 获取"""
        ...

    async def install(self, manifest_yaml: str, owner_id: UUID) -> Skill:
        """
        安装 Skill：
        1. Parse YAML manifest
        2. Validate: 检查 required_tools 存在
        3. Trust Review: 调用 Policy Engine 检查 risk
        4. 创建 skill + skill_version 记录
        5. 状态: R3+ 的 Skill 置为 'review'，否则 'active'
        """
        ...

    async def create_draft(
        self,
        candidate: SkillCandidate,  # 来自 Reflection
        owner_id: UUID,
    ) -> Skill:
        """
        Agent 自动生成的 Skill Draft：
        status = 'draft'
        source = 'agent_generated'
        需要用户审批才能变为 'active'
        """
        ...

    async def approve(self, skill_id: str, owner_id: UUID) -> Skill:
        """用户审批通过的 Skill: draft → active"""
        ...

    async def update(
        self,
        skill_id: str,
        new_manifest_yaml: str,
        owner_id: UUID,
    ) -> Skill:
        """版本更新：创建新 skill_version，更新 current_version"""
        ...

    async def disable(self, skill_id: str, owner_id: UUID) -> None:
        """禁用 Skill: active → disabled"""
        ...

    async def evaluate(
        self,
        skill_id: str,
        run_id: UUID,
        success: bool,
        feedback: str | None,
    ) -> None:
        """记录 Skill 使用反馈，用于未来的 Skill 质量评分"""
        ...
```

---

## 4. Skill 生命周期

```
                    ┌──────────┐
                    │  Install  │ （用户手写或内置）
                    └────┬─────┘
                         ↓
                    ┌──────────┐
                    │ Validate  │ （检查 required_tools / manifest 格式）
                    └────┬─────┘
                         ↓
                    ┌──────────┐
                    │  Trust    │ （R3+ Skill 需要额外审查）
                    │  Review   │
                    └────┬─────┘
                         ↓
                    ┌──────────┐
                    │  Enable   │ → status = 'active'
                    └────┬─────┘
                         ↓
                    ┌──────────┐
                    │   Use     │ （被 Context Engine 检索和注入）
                    └────┬─────┘
                         ↓
                    ┌──────────┐
                    │ Evaluate  │ （记录使用成功率 / 用户反馈）
                    └────┬─────┘
                         ↓
                    ┌──────────┐
                    │  Update   │ （新版本，或基于反馈改进）
                    └──────────┘
```

### Agent 自动生成的 Skill

```
Experience（Run 完成）
    ↓
Reflection（异步分析 what_worked / reusable_knowledge）
    ↓
Candidate Skill（Agent 自动生成的 draft）
    ↓ status = 'draft'
User Approval（用户审查、修改、批准）
    ↓
Active
```

**第一版不允许 Agent 无审查地安装第三方 Skill 或自动激活自己生成的 Skill。**

---

## 5. Skill Candidate（来自 Reflection）

```python
class SkillCandidate(BaseModel):
    """Reflection 阶段产出的 Skill 候选"""
    source_run_id: UUID

    name: str
    description: str
    triggers: list[str]
    required_tools: list[str]
    instructions: str  # Markdown 格式的步骤说明

    confidence: float  # 自动生成的可信度
    evidence: str      # 为什么认为这是一个有用的 Skill（引用 Run 中的经验）
```

---

## 6. Self-Improvement Loop（与 Department 01 配合）

真正值得做的"自我成长"不是 Agent 修改自己的源代码，而是：

```
Experience（任务执行）
    ↓
Reflection（异步分析，可用便宜模型）
    ↓ 输出:
    { what_worked, what_failed, reusable_knowledge,
      memory_candidates, skill_candidates }
    ↓
Memory Candidates → Memory System (Dept 05)
Skill Candidates  → Skill Engine（Dept 07）→ draft → user review
    ↓
Human Approval
    ↓
Skill Update（Active）
```

---

## 7. 第一批内置 Skills

```text
skills/builtin/
├── github_weekly_report.yaml     — 生成 GitHub 周报
├── morning_brief.yaml            — 每日早间摘要
├── project_onboarding.yaml       — 了解新项目
├── meeting_prep.yaml             — 会议准备（查日历 + 笔记 + 公开资料）
└── code_review_checklist.yaml    — 代码审查清单
```

---

## 8. 详细任务列表

### Task 1: 定义数据模型
- [ ] `skills` 表 + migration
- [ ] `skill_versions` 表 + migration
- [ ] `Skill` / `SkillVersion` Pydantic models
- [ ] `SkillCandidate` model

### Task 2: 实现 Skill Manifest Parser
- [ ] YAML manifest → Pydantic model validation
- [ ] required_tools 存在性检查
- [ ] trigger 列表去重和标准化

### Task 3: 实现 SkillEngine
- [ ] `search()` — trigger keyword match + embedding match
- [ ] `install()` — parse → validate → trust review → save
- [ ] `create_draft()` — agent generated draft
- [ ] `approve()` / `update()` / `disable()`
- [ ] `evaluate()` — 记录使用反馈

### Task 4: 实现 Skill 上下文注入（配合 Context Engine）
- [ ] Context Engine 调用 `SkillEngine.search()` 时
- [ ] 返回 active skill 的 `instructions`（Markdown）
- [ ] Context Engine 注入到 Tier 2 或 Tier 3 的 prompt

### Task 5: 编写内置 Skills
- [ ] `github_weekly_report` — GitHub 周报
- [ ] `morning_brief` — 早间摘要
- [ ] `project_onboarding` — 项目上手

### Task 6: 测试
- [ ] Skill 安装 → 校验 → 激活 完整流程
- [ ] Agent 生成 draft → 用户审批 → active
- [ ] Skill search 匹配测试（trigger 匹配准确率）
- [ ] 禁用 Skill 后不出现在搜索结果

---

## 9. 验收标准

### Skill 触发测试
```text
User query: "帮我总结这周的 GitHub 工作"
    ↓
SkillEngine.search("总结这周的 GitHub 工作")
    ↓
返回 github_weekly_report skill (matched trigger "weekly report")
    ↓
Context Engine 将 skill.instructions 注入 prompt
```

### Agent 自动生成 Skill 测试
```text
Reflection 产出 SkillCandidate（name: "astrbot_debug", triggers: ["astrbot error"]）
    ↓
SkillEngine.create_draft(candidate)
    ↓
新 Skill status = 'draft', source = 'agent_generated'
    ↓
用户 approve → status = 'active'
    ↓
下次用户说 "astrbot 报错了" → 自动匹配此 Skill
```

---

## 10. 参考蓝图章节

- Section 26: Skills（Manifest 定义）
- Section 27: Skill 生命周期
- Section 70: Self-improvement
- Section 71: Reflection
- Section 9.5: Procedural Memory / Skill
