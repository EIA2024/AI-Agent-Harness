# 03 — 开源 Agent CLI 深度对比与可迁移模式

> 本文只提取“交互原则”，不要求做 feature parity。  
> 参考仓库在 2026-08-11 时可能继续演进，实施时以官方仓库最新代码为准。

## 1. OpenAI Codex CLI

### 观察
Codex Rust CLI 把 `core / exec / tui / cli` 分开；TUI 内又继续拆分：
- app/event
- chat widget
- bottom pane
- approval events
- diff render
- exec cells
- markdown streaming/render
- session resume/picker/state
- status
- keymap/key hints
- terminal capability/palette/title
- transcript/reflow
- token usage
- snapshots/tests

Slash command 也作为独立领域，包含：
`/model /permissions /review /new /resume /compact /plan /diff /status /mcp /quit ...`

配置还显式考虑：
- alternate screen auto/always/never；
- keymap context；
- terminal capability；
- accessibility/notifications 等。

### 可迁移
1. **TUI 是一个 presentation subsystem，不是 main loop。**
2. **session picker / approval / diff / streaming 都要独立组件。**
3. **命令菜单按使用频率排序，不按字母排序。**
4. **inline/alternate screen 是用户环境选择，不要硬编码。**
5. **headless exec 与 TUI 分离。**

### 不应照搬
- Git diff、sandbox、IDE integration 是 coding-agent 特性；
- Personal AI OS 应先把 memory/policy/audit 做成一等 UX。

---

## 2. Kimi Code CLI

### 观察
Kimi 的主命令把交互和非交互边界定义得很清楚：
- `kimi` 交互；
- `--continue` 最近 session；
- `--session [id]` selector/直接恢复；
- `-p` 单次 headless；
- `--output-format text|stream-json`；
- `--plan`；
- 权限模式；
- `--add-dir`；
- subcommands（login/provider/doctor/export/ACP 等）。

非交互模式强调：
- assistant -> stdout；
- thinking/tool progress/resume notice -> stderr；
- `stream-json` -> JSONL stdout。

工具 UI 的关键模式：
- read-only 默认可执行；
- write/bash 需要 approval；
- Bash stdout/stderr 实时进入 running tool card；
- TodoList 可见；
- Plan 退出时用户审批；
- background task 有状态与 output path。

### 可迁移
1. **恢复 Session 是主路径，不是隐藏管理命令。**
2. **stdout/stderr 是稳定契约。**
3. **Tool 是持续状态 card，不是一行日志。**
4. **Plan/permission 是显式 mode。**
5. **doctor 是一等命令。**

### 不应照搬
当前 Runtime 没有通用 Bash、agent swarm 等对应能力，不要因为 UI 参考而新增大范围工具。

---

## 3. Gemini CLI

### 观察
Gemini CLI 同样提供：
- interactive；
- `-p` non-interactive；
- `text/json/stream-json`；
- resume/list sessions；
- approval mode；
- sandbox；
- include directories；
- screen-reader mode；
- checkpointing；
- Plan Mode；
- slash commands；
- MCP/extensions。

### 可迁移
1. **机器输出至少 text + final JSON + streaming JSONL。**
2. **accessibility 是正式 flag/config，不是“以后再做”。**
3. **checkpoint/recovery 应纳入 Agent CLI 设计。**
4. **command surface 与 TUI slash commands 可以映射同一 command registry。**

---

## 4. OpenCode

### 观察
OpenCode TUI 强调：
- 当前目录即 project 上下文；
- `@` fuzzy file reference；
- model/agent 选择；
- session/project navigation；
- terminal-native coding workflow。

### 可迁移
- “current working directory = 最自然的 session/project scope”；
- fuzzy picker 比让用户输入 UUID 更符合人类交互；
- reference completion 是未来 context UX 的好方向。

### 不应照搬
本项目的 context 不只文件，还包括 memory / tools / external sources，因此未来应设计通用 `@context` 机制，而不局限文件。

---

## 5. GLM / Z.AI 生态

### 观察
Z.AI 的官方 Coding Plan 更像 **model/provider 能力层**，官方文档重点展示在 Claude Code、OpenCode、Cline 等现有 harness 中使用 GLM，而不是提供一个需要复刻的唯一第一方 TUI。

这给本项目一个重要启示：
- **Provider UX 与 Harness UX 分离。**
- 用户选择 GLM/Moonshot/OpenAI/Anthropic，不应改变 CLI 的交互语义。
- Provider 差异应在 capability layer 处理（thinking、tool calling、multimodal、context window 等）。

### 可迁移
- provider profile / login / model selection 做成稳定抽象；
- TUI 状态栏显示 `profile/model`，但 tool/policy/session 语义不跟 provider 绑定。

---

## 6. 共识模式

| 模式 | Codex | Kimi | Gemini | 建议 |
|---|---|---|---|---|
| 默认交互 TUI | 是 | 是 | 是 | 必须 |
| Headless | exec | -p | -p | 必须 |
| JSONL stream | 有机器模式 | stream-json | stream-json | 必须 |
| Session resume | 强 | 强 | 强 | 必须 |
| Approval UI | 强 | 强 | 强 | 必须 |
| Plan mode | 是 | 是 | 是 | P2 |
| Slash commands | 强 | 有 | 强 | 必须 |
| Tool card | 强 | 强 | 强 | 必须 |
| Doctor | 有诊断 | 有 | 有配置诊断 | 必须 |
| Accessibility | terminal handling | 部分 | screen-reader | 必须 |
| Provider abstraction | 有 | 有 | 主要 Gemini | 项目已有，强化 |

---

## 7. 最重要的迁移原则

### 原则 A：复制“边界”，不复制“皮肤”
最值得学的是：
- event boundary；
- permission boundary；
- interactive/headless boundary；
- session boundary；
- renderer boundary。

### 原则 B：Personal AI 的状态比代码 diff 更重要
优先让用户看到：
`session / run / memory / approval / audit / tool`
而不是优先做漂亮的 git UI。

### 原则 C：配置不改变基本语义
无论 OpenAI-compatible、Anthropic、Moonshot、GLM，用户都应看到同一套：
`Run -> Tool -> Approval -> Result`。
