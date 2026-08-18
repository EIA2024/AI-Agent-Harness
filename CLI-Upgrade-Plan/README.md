# AI-Agent-Harness CLI v2 升级方案 — 主路由

> 面向仓库：`EIA2024/AI-Agent-Harness`  
> 调研与方案日期：2026-08-11  
> 文档定位：**给执行 Agent 使用的工程实施包，而不是概念性 UI 提案。**

> **历史状态（2026-08-17）**：CLI v2 已合入 `main`。本目录保留原始设计与任务卡，
> 其中未勾选清单不代表当前实现状态。现役用户合同见
> [`docs/cli-v2.md`](../docs/cli-v2.md)，架构与后续修复记录见
> [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) 和
> [`docs/agent-memory/README.md`](../docs/agent-memory/README.md)。

## 0. 一句话目标

把当前 `apps/cli/main.py` 中的“`argparse + print + 手写 ANSI + 手写 SSE` 聊天客户端”，升级成一个以 **Run 状态机、Session、Memory、Tool、Policy/Approval、Audit** 为核心的一等终端操作界面，同时保留稳定的 headless/CI 接口。

最终用户应该能够：

- 直接运行 `personal-ai` 进入交互式 TUI；
- 随时看懂 Agent 当前在 **做什么、为什么停住、调用了什么、产生了什么结果、下一步需要谁做决定**；
- 在审批点执行 `approve / reject / edit arguments / cancel`；
- 可靠恢复历史 Session 与等待审批的 Run；
- 查看工具、记忆、上下文、模型、权限和审计状态；
- 在脚本/CI 中使用稳定的 `text / json / stream-json` 输出，而不会被 spinner、thinking、日志污染 stdout；
- 在窄终端、无颜色、非 TTY、screen reader 等环境中仍然可用。

---

# 1. Agent 如何阅读本方案

不要从头到尾一次性加载全部文件。按任务路由读取。

## 1.1 第一次执行必须读

1. `00_EXECUTION_PROTOCOL.md`
2. `01_CURRENT_STATE_AUDIT.md`
3. `02_FIRST_PRINCIPLES.md`
4. `17_IMPLEMENTATION_PHASES.md`
5. 当前 Phase 对应的子方案
6. `18_AGENT_TASK_CARDS.md` 中对应 Task Card
7. `19_ACCEPTANCE_CHECKLIST.md`

## 1.2 按问题路由

| 你正在处理的问题 | 必须读取 |
|---|---|
| 不清楚为什么这么设计 | `02_FIRST_PRINCIPLES.md`, `03_REFERENCE_RESEARCH.md` |
| CLI 命令怎么改 | `04_TARGET_UX_INFORMATION_ARCHITECTURE.md`, `07_INTERACTION_COMMAND_KEYMAP.md`, `12_NON_INTERACTIVE_AUTOMATION.md` |
| Python 文件怎么拆 | `05_TARGET_TECHNICAL_ARCHITECTURE.md` |
| TUI 长什么样 | `06_TUI_VISUAL_SPEC.md`, `wireframes/*` |
| SSE/流式输出 | `08_STREAMING_EVENT_RENDERER.md`, `ADR/ADR-002-event-model.md` |
| Approval / 风险展示 | `09_APPROVAL_POLICY_SECURITY_UX.md`, `ADR/ADR-004-permission-model.md` |
| Session / Memory | `10_SESSION_CONTEXT_MEMORY_UX.md` |
| Tool / Diff / Artifact | `11_TOOL_DIFF_ARTIFACT_UX.md` |
| CI / pipe / JSONL | `12_NON_INTERACTIVE_AUTOMATION.md`, `ADR/ADR-003-output-channels-jsonl.md` |
| Provider / Config / MCP | `13_CONFIGURATION_PROVIDER_MCP.md` |
| doctor / 日志 / telemetry | `14_OBSERVABILITY_DIAGNOSTICS.md` |
| 测试怎么写 | `15_TESTING_QUALITY_GATES.md` |
| 如何兼容旧 CLI | `16_MIGRATION_COMPATIBILITY.md` |
| 精确到文件怎么拆 | `20_FILE_CHANGE_MAP.md` |
| 直接交给 Agent 开工 | `AGENT_START_PROMPT.md` |
| 选择 TUI 框架 | `ADR/ADR-001-tui-framework.md` |

---

# 2. 方案总纲

## 2.1 不重写 Runtime

当前架构已经把核心能力分层：

`CLI/API -> Gateway -> Agent Runtime -> Context / Model / Tool Broker / Memory -> DB`

升级首先发生在 CLI 和 API 投影层。**不要为了 UI 重写 LangGraph、ToolBroker、Memory Engine。**

## 2.2 先补“能力投影”，再做“高级能力”

优先级：

### P0：CLI-only / 低后端风险
- CLI 模块化
- 统一 API Client
- 规范 SSE Decoder
- typed UI events
- transcript cells
- session picker
- approval approve/reject/edit
- run cancel
- Rich/TUI 渲染
- `text/json/stream-json`
- stdout/stderr 分离
- diagnostics
- CLI tests

### P1：小型 API 扩展
- `GET /v1/runs` 正式列表接口
- 统一 SSE event envelope / sequence
- approval.required 事件包含可关联信息
- session detail 提供足够的 context/status 摘要
- tool call 事件生命周期一致

### P2：需要产品/安全决策的 Runtime/API 扩展
- 显式 Plan Mode
- session-scoped approval mode
- background run / notification
- artifact browser
- MCP 管理
- richer token/cost/context usage

P0 未通过质量门禁前，不执行 P2。

---

# 3. 目标架构摘要

```text
personal-ai
│
├── command layer (Typer)
│   ├── interactive -> Textual TUI
│   ├── exec         -> headless runner
│   └── admin        -> sessions/runs/memory/tools/audit/config/doctor
│
├── client layer
│   ├── AsyncAPIClient
│   ├── SSE transport
│   └── API DTOs
│
├── presentation domain
│   ├── normalized UIEvent
│   ├── reducer
│   ├── AppState
│   └── selectors
│
├── interactive presentation
│   ├── transcript
│   ├── composer
│   ├── status bar
│   ├── tool cells
│   ├── approval modal
│   └── pickers / overlays
│
└── headless presentation
    ├── text
    ├── json
    └── JSONL stream
```

**核心边界：网络 transport 不能直接 print，组件不能直接发 HTTP，stdout renderer 不能依赖 TUI。**

---

# 4. 最终 CLI 入口建议

```bash
personal-ai                         # 默认进入 TUI，新建/恢复当前 project session
personal-ai --continue              # 恢复当前目录最近 session
personal-ai --session               # 打开 session picker
personal-ai --session <id>          # 直接恢复
personal-ai exec "..."              # headless 单任务
personal-ai exec "..." -o json
personal-ai exec "..." -o stream-json
personal-ai sessions list
personal-ai runs list
personal-ai runs show <id>
personal-ai runs cancel <id>
personal-ai approvals list
personal-ai approvals approve <id>
personal-ai approvals reject <id>
personal-ai approvals edit <id>
personal-ai memories ...
personal-ai tools list
personal-ai audit list
personal-ai config ...
personal-ai doctor
```

旧的 `chat / send / approve --list` 在兼容期保留并输出 deprecation hint，不立即删除。

---

# 5. 最终交互模型

终端里的每一项不是“打印一行”，而是一种 typed transcript cell：

- `UserCell`
- `AssistantCell`
- `ReasoningSummaryCell`
- `ToolCell`
- `DiffCell`
- `ApprovalCell`
- `RunStatusCell`
- `MemoryNoticeCell`
- `ErrorCell`
- `SystemNoticeCell`

底部固定只有两类东西：

1. Composer（输入）
2. Status/Key Hint（当前 session / model / permission / run state）

其他信息通过 transcript 或 overlay 呈现，避免永久 side panel 导致小终端拥挤。

---

# 6. 关键设计决策

1. **默认不展示原始 chain-of-thought。** 当前 `thinking.delta` 只作为兼容 transport 输入；产品 UI 应显示安全的简短 progress/reasoning summary。
2. **权限永远以服务端 Policy Engine 为真源。** CLI 不通过“自动点击 approve”伪造 YOLO；未来自动权限模式必须由服务端策略显式支持。
3. **非交互 stdout 是 API。** 人类进度、tool 状态、spinner、恢复提示走 stderr；JSONL schema 要版本化。
4. **Approval 是一等交互态。** 它不是一行警告，而是 Run 状态机的阻塞节点。
5. **Session 恢复优先按工作目录与最近活跃度。** 用户不应该手工复制 UUID 才能继续工作。
6. **TUI 只消费 normalized event。** live SSE 与 DB replay 的差异在 transport/normalizer 层消除。
7. **宽度与终端能力是输入变量。** 不依赖 emoji、truecolor 或鼠标才能完成关键任务。
8. **兼容性优先于视觉重写。** 先建边界与测试，再替换体验。

---

# 7. 实施阶段

| Phase | 目标 | 风险 | 入口 |
|---|---|---:|---|
| 0 | 基线、契约、测试支架 | 低 | `17_IMPLEMENTATION_PHASES.md#phase-0` |
| 1 | CLI Core：API/SSE/event/headless | 中 | `08`, `12`, `15` |
| 2 | Interactive TUI MVP | 中 | `05`, `06`, `07` |
| 3 | Approval/Session/Memory/Tools | 中 | `09`, `10`, `11` |
| 4 | API event/runs 小扩展 | 中 | `08`, `17` |
| 5 | Config/Doctor/Audit/Accessibility | 低-中 | `13`, `14` |
| 6 | 高级模式（Plan/Permission/Background） | 高 | P2，单独 ADR |

---

# 8. Definition of Done

CLI v2 不是“画面能跑起来”就完成。至少满足：

- 旧核心命令不发生无提示破坏；
- interactive 与 headless 共用同一 transport/domain 层；
- SSE live/replay 都通过 contract tests；
- approval 能 approve/reject/edit，并在 resume 后继续收到事件；
- Ctrl+C 可取消 active run，不导致数据库残留错误状态；
- `exec -o stream-json` 每行都是合法 JSON；
- stdout 不被日志/progress 污染；
- 60/80/120/160 列宽 snapshot 全部通过；
- `NO_COLOR=1` 与非 TTY 可用；
- API 断开、401、404、409、500、stream EOF 都有明确恢复路径；
- CLI 关键模块有单测，主流程有 integration test；
- `ruff` + `pytest` 全绿；
- 文档、`--help` 与实际命令一致。

详细门禁见 `19_ACCEPTANCE_CHECKLIST.md`。
