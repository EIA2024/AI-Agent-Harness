# 04 — 目标 UX 与信息架构

## 1. 三种运行表面

### Surface A — Interactive TUI
`personal-ai`

适合：
- 长对话；
- tool 使用；
- approval；
- session 恢复；
- memory/context 检查。

### Surface B — Headless Exec
`personal-ai exec PROMPT`

适合：
- shell；
- CI；
- cron；
- Agent-to-Agent；
- capture output。

### Surface C — Administrative Commands
`sessions/runs/approvals/memories/tools/audit/config/doctor`

适合确定性 CRUD/inspection，不必进入 TUI。

---

## 2. TUI 主信息架构

```text
┌─ Context line ─────────────────────────────────────────────┐
│ project · session · model · permission · run state         │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Transcript                                               │
│  You / Agent / Tool / Approval / Error / Notice           │
│                                                            │
├────────────────────────────────────────────────────────────┤
│ Composer                                                   │
│ > Ask or type / for commands                               │
├────────────────────────────────────────────────────────────┤
│ key hints · context/compaction · background/approval state │
└────────────────────────────────────────────────────────────┘
```

### 为什么不默认三栏
- Personal Agent 是事件时间线；
- tool/approval 需要因果顺序；
- 小终端常见；
- side pane 会让自然语言换行过密。

Detail 信息用 overlay/pager。

---

## 3. 启动路径

### `personal-ai`
1. 立即初始化 UI shell；
2. 读取本地 profile；
3. 后台 `healthz`；
4. 查 current project 最近 session；
5. 若唯一明显候选 -> 提示 continue/new；
6. 若多候选 -> selector；
7. 若 API 不可达 -> offline error screen + retry/doctor。

不要启动时打印大 banner 占滚动区。

---

## 4. Session selector

每行显示：
```text
● Fix memory retrieval regression
  12 min ago · waiting approval · GLM-4.7 · 14 turns

  API refactor discussion
  yesterday · completed · kimi-for-coding · 32 turns
```

排序：
1. cwd/project match
2. waiting_approval/running
3. updated_at
4. title

搜索字段：
- title
- short id
- model
- status
- date

---

## 5. Transcript semantic hierarchy

### User
最高视觉识别但不需要 box 满屏。

### Assistant
最终答案正常前景色，Markdown 渲染。

### Progress/Reasoning Summary
低强调、短句、可折叠。
永远不要与 final answer 同视觉权重。

### Tool
显示：
`状态 + tool + action summary + elapsed`

### Approval
高强调，必须显示 action 与 risk，获得焦点。

### Error
“问题 + 恢复动作”，而不是 stack trace。

---

## 6. TUI 中的 slash commands

核心 P0：
- `/help`
- `/status`
- `/new`
- `/sessions`
- `/resume`
- `/model`
- `/provider`
- `/approvals`
- `/tools`
- `/memory`
- `/context`
- `/cancel`
- `/clear`
- `/exit`

P1/P2：
- `/compact`
- `/plan`
- `/permission`
- `/audit`
- `/export`
- `/theme`

Slash command palette：
- fuzzy filter；
- 按频率排序；
- 当前状态不可用的命令置灰并说明原因；
- command registry 同时为 `--help` 与 TUI completion 提供 metadata。

---

## 7. 命令树

```text
personal-ai
├── exec
├── sessions
│   ├── list
│   ├── show
│   ├── rename
│   └── archive
├── runs
│   ├── list
│   ├── show
│   ├── cancel
│   └── resume
├── approvals
│   ├── list
│   ├── show
│   ├── approve
│   ├── reject
│   └── edit
├── memories
│   ├── list/search/show/edit/forget
├── tools
│   └── list/show
├── audit
│   └── list
├── config
│   └── init/list/show/use/edit/remove
└── doctor
```

---

## 8. 命名原则

- `exec` = 一次性 headless Agent run；
- `runs show` 不叫 `get`，面向人类 CLI；
- `approvals` 使用复数资源名；
- destructive command 使用动词 (`forget/archive/cancel`)；
- UUID 都允许 shortest-unique prefix（若服务端/客户端安全解析）；
- 输出中默认显示 short id，复制/JSON 才是 full id。
