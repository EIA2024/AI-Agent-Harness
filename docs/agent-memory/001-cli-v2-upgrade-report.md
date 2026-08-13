# 001 — CLI v2 升级报告（过程记忆）

> **主题**：CLI v2 升级全过程记录（基线 → 架构 → 测试 → 对抗审查 → 后续事项）
> **日期**：2026-08-12
> **分支/提交范围**：`worktree-composed-giggling-ocean`，`4105b3b..2c9ca87`（9 个提交）
> **方案来源**：`CLI-Upgrade-Plan/`
>
> 这是项目过程记忆，不是工作区规则。跨模块硬性约束见仓库根 `CLAUDE.md`。

---

## 1. 一句话现状

本仓库是 **Personal AI OS**——一个长期运行的个人 Agent 运行时（LangGraph + FastAPI + 持久化 + 工具/审批/记忆）。本次工作把旧 CLI（`apps/cli/main.py` 519 行单体，argparse + 同步 httpx + 手写 SSE）升级为 **CLI v2**：模块化的 `personal_ai_os/cli/` 包，含交互式 Textual TUI、headless `exec`、完整管理命令、严格 stdout/stderr 契约、终端安全消毒。

**质量门禁**：461 个测试全绿（含全部 299 个既有测试），`ruff check src apps tests` 干净。

---

## 2. 升级方案在哪里

`CLI-Upgrade-Plan/`（仓库根目录）—— 原始工程实施包，含 21 个编号文档 + ADR + wireframes：

- 必须读：`README.md`, `00_EXECUTION_PROTOCOL.md`, `17_IMPLEMENTATION_PHASES.md`, `18_AGENT_TASK_CARDS.md`, `19_ACCEPTANCE_CHECKLIST.md`
- 按问题路由：见 `CLI-Upgrade-Plan/README.md` §1.2

**执行协议关键规则**（`00_EXECUTION_PROTOCOL.md`）：
- 一次一个 Task Card，先读后改，每个 Task 一个 commit（`cli-v2(Txx): ...`）
- 不重写 Runtime（LangGraph/ToolBroker/Memory/Policy 不动）
- **CLI 不是权限真源**——审批必须走服务端 API，禁止客户端模拟 YOLO
- 默认不展示 raw chain-of-thought
- 所有 terminal 输出必须防 ANSI/OSC/control-sequence 注入
- 遇到安全/权限/兼容边界 → 停止并建 ADR

---

## 3. 新 CLI 架构与文件地图

代码位置：`src/personal_ai_os/cli/`（已进入 wheel，`[project.scripts] personal-ai`）。`apps/cli/main.py` 只是兼容 shim。

```
src/personal_ai_os/cli/
├── __init__.py         # 版本号 2.0.0.dev0
├── main.py             # Typer 根命令（只做参数解析与分发）+ legacy 别名
├── legacy.py           # pre-v2 argparse CLI，保留作兼容路径（chat/send/config wizard）
├── bootstrap.py        # 环境配置 + client 工厂（PERSONAL_AI_API_URL/KEY）
├── api/
│   ├── client.py       # AsyncAPIClient（typed errors，无 print/SystemExit）
│   ├── dto.py          # Session/Run/Approval/Memory/Tool/Audit/Message DTO（宽容构造）
│   ├── errors.py       # APIError/AuthError/NotFound/Conflict/RateLimit/Server/Transport/StreamProtocol/Config
│   └── sse.py          # SSEDecoder（WHATWG 合规：多行 data/注释/EOF flush）
├── domain/
│   ├── events.py       # UIEventType + UIEvent（UI 只消费 UIEvent，不直接吃 SSE）
│   ├── normalizer.py   # ServerEvent→UIEvent（live/replay 统一、raw thinking 抑制）
│   ├── state.py        # AppState/ToolViewState/TranscriptCell + CELL_* 常量
│   └── reducer.py      # reduce(state, event) 纯函数（确定性、可重放）
├── commands/
│   ├── exec.py         # headless 单跑管线（send→stream→normalize→reduce→render）
│   ├── sessions/runs/approvals/memories/tools_audit/config/doctor
│   ├── table_output.py # Rich 表格（已消毒）+ JSON 输出
│   └── run_helper.py   # run_admin()：async 管理命令 + 干净错误→退出码
├── controllers/chat.py # TUI/plain 共用的控制器（send/cancel/approve/reject/edit+resume）
├── output/
│   ├── text.py         # final_answer / render_transcript（消毒）
│   ├── json_output.py  # run_summary（exec -o json 契约）
│   ├── jsonl.py        # JSONLWriter（exec -o stream-json，v=1 schema，消毒）
│   └── exit_code.py    # ExitCode 枚举 + 状态→退出码映射
├── tui/
│   ├── app.py          # PersonalAIApp（Textual）：status/transcript/composer/footer
│   ├── command_registry.py  # slash 命令注册表
│   ├── screens/        # approval modal、info overlay
│   ├── widgets/        # Transcript/StatusBar/Composer（Enter 提交、Ctrl+J 换行）
│   └── render.py       # 纯渲染（可单测）+ 终端能力检测
├── sanitize.py         # strip_control_sequences / redact_secrets / truncate_long_lines
└── notify.py           # opt-in 审批通知（PERSONAL_AI_NOTIFY，仅 BEL）
```

**核心数据流**（所有路径一致）：
`HTTP/SSE → ServerEvent → SSEDecoder → Normalizer → UIEvent → Reducer → AppState → 渲染（TUI 或 headless）`

---

## 4. 设计约束（本次升级确立的不变量）

1. **API/transport 层永不 print、永不 SystemExit**——只 return/yield/raise typed exception。
2. **服务端 Policy Engine 是权限真源**。CLI 只显示 + 提交用户决策；审批 edit 必须走服务端 hash-binding / resume。
3. **stdout 是契约**：`exec` 的 stdout 只含结果协议（text=最终答案 / json=一个对象 / stream-json=JSONL）；进度/诊断一律 stderr。退出码稳定（0/1/2/3/4/5/6/7/8，见 `output/exit_code.py`）。
4. **raw chain-of-thought 默认不展示**（`thinking.delta` → 抑制的 progress 事件，三层门控）。
5. **所有终端输出过 sanitize**（TUI 渲染、headless text、JSONL、Rich 表格、审批 modal、tool 详情、exec stderr）。
6. **交互与 headless 共用 transport/domain，不共用 presentation**——CI 永不依赖 TTY。
7. TUI 组件不发 HTTP（由 controller 驱动）；transport 不做渲染。

---

## 5. 服务端改动（Phase 4 投影 + 对抗审查修复）

- `apps/api/routers/runs.py` — 新增 `GET /v1/runs`（owner-scoped、`session_id`/`status` 过滤、`limit`/`offset` 分页）。
- `apps/api/routers/stream.py` — **versioned SSE envelope**（`schema_version/event_id/seq/timestamp/run_id`），live 与 replay 共用 `_envelope`；replay 的 `approval.required` 从 DB 富化。
- `apps/api/routers/sessions.py` + `serializers.py` — session list 增加 `message_count` + `active_run_status`（批量查询，无 N+1）。
- `apps/api/routers/approvals.py` — **修复 C1**：`_resolve()` 先检查 `pending` 再调引擎、读回最终态——真实 ApprovalEngine 下首次 approve/reject/edit 不再 409，二次操作返回 409。
- `src/personal_ai_os/agent_runtime/runner.py` — **修复 C2**：`resume` 容忍已解决的审批（不再 500）；**修复 M3**：`cancel` 推送 live `run.cancelled` + 取消后台任务（CLI Ctrl+C 不再挂起）。
- `src/personal_ai_os/agent_runtime/graph.py` — **T42**：`tool_request`/`approval` 节点推送 live `tool.started/completed/failed`（稳定 `tool_call_id`）；**修复 M2**：`approval` 节点接受 `approved_with_edits`（编辑参数真正生效）。

> 注意：这些服务端改动需要重启本地 API 服务才生效。运行中的旧服务没有 `/v1/runs`，`runs list` 会优雅报错（exit 8）。

---

## 6. 命令面

```
personal-ai                        # 交互 UI（TTY→Textual；pipe/NO_COLOR→plain 回退）
personal-ai --continue | --session <id> | --pick   # 会话恢复（无需手抄 UUID）
personal-ai exec "..." [-o text|json|stream-json] [--session id]   # headless
personal-ai sessions list/show
personal-ai runs list/show/cancel/resume        # list 用 /v1/runs（T40）
personal-ai approvals list/approve/reject/edit
personal-ai memories list/search/show/forget    # forget 需 --yes（方案强制确认）
personal-ai tools list / audit list
personal-ai config init/list/show/use/edit/remove/path/sources/validate
personal-ai doctor [--json]
```

Legacy 别名（保留，输出 deprecation 警告到 stderr）：`chat`、`send`（→exec）、`approve`（→approvals，`--list` 与 `<id>` 均已修复为可用且 `approve <id>` 输出 JSON）、`runs get`（→show）。

TUI slash 命令：`/help /status /context /memory /tools /approvals /cancel /clear /new /exit`。

环境变量：`PERSONAL_AI_API_URL`（默认 `http://localhost:8000`）、`PERSONAL_AI_API_KEY`（默认 `dev-key`）、`PERSONAL_AI_CONFIG_DIR`、`NO_COLOR`、`PERSONAL_AI_NOTIFY`、`REDUCED_MOTION`。

---

## 7. 测试

```
tests/unit/cli/          SSE decoder、API client、normalizer、reducer、output 契约、
                         sanitize、TUI render、TUI app (run_test)、approval modal、
                         performance 压测、hardening（doctor/notify）
tests/integration/cli/   exec 端到端（ASGITransport + 真实 runtime + scripted model）、
                         tool 生命周期（排干 live 队列）、runs list、session 元数据、
                         真实 ApprovalEngine 的 approve/reject/edit/resume 回归
tests/unit/cli/fixtures/ 11 个 *.sse fixture + sse_fixtures.py（reduce_fixture helper）
tests/snapshots/         （预留，尚未使用）
```

运行：`uv run ruff check src apps tests`、`uv run pytest -q`（Windows 下用 `.venv/Scripts/python.exe -m pytest`）。
CLI 帮助冒烟：`tests/unit/test_cli_help.py`（subprocess 黑盒，已加 `encoding="utf-8"`）。

**Windows 特别注意**：Rich 帮助输出的 Unicode 边框在 GBK 控制台会解码失败——`main.py` 的 `_ensure_utf8()` 在 `app()` 前把 stdout/stderr reconfigure 为 utf-8，任何新入口都必须保持这一顺序。

---

## 8. 对抗性审查（已完成）与修复

2026-08-12 对全部升级代码做了 3 个独立代理的对抗审查（安全/兼容/流式），发现并修复：

| 严重度 | 发现 | 修复 |
|---|---|---|
| CRITICAL | 真实 ApprovalEngine 下审批 API 首次调用即 409（C1）；resume 二次 resolve 500（C2） | approvals router 顺序 + runner.resume 幂等（见 §5） |
| HIGH | Rich Table / TUI Static / exec stderr 未消毒 → 终端注入 | 全部接入 sanitizer |
| HIGH | `approve --list` 别名传 Option 对象 → 恒空 | 直接调底层 async 函数 |
| HIGH | 流无终态 EOF 时 exec 静默 exit 0 空输出（F1.1） | 视为协议错误 exit 8 |
| HIGH | 服务器 `event:error` 被当未知事件（F1.2） | normalizer 增加 `_on_error` → ERROR |
| HIGH | 审批状态机死锁/粘窗（F3.1/F3.4/F2.1） | pending 只在 resume 成功后才清；terminal/cancel/dismiss 时清 |
| MEDIUM | `approved_with_edits` 被当拒绝（M2） | graph approval 节点接受该决策 |
| MEDIUM | cancel 不终止 live 流（M3） | cancel 推送 run.cancelled + 取消任务 |
| MEDIUM | bidi 覆盖符、超长行、list 内密钥未遮蔽（M4/M5/M7） | sanitize 增强 |
| MEDIUM | 非 JSON 200 响应裸 traceback（F4.2） | 映射 StreamProtocolError |
| MEDIUM | replay 后重复 tool 卡片（F3.3） | reducer 幂等 |

这些修复的回归测试见 `tests/integration/cli/test_approval_real_engine.py` 等。

---

## 9. 已知限制 / 未完成项

1. **Phase 6（高级模式）未执行**——六个项目均被方案门禁要求"单独 ADR + 威胁模型"：
   - **服务端 Approval Modes**（价值最高，解锁 headless 自动化；服务器当前无 `approval_mode`/`auto_policy`）
   - 显式 Plan Mode（运行时已有 planner，缺显式呈现+批准交互）
   - 后台 run + 通知（`services.scheduler` 未接线，automations run 返回 501）
   - Artifact 浏览器（DB 有 `artifacts` 表，但当前无任何运行时写入）
   - MCP 管理（当前是 native connector 架构，MCP 是条件性未来整合）
   - 多 Agent 展示（运行时是单 Agent，无 swarm 能力）
2. **TUI `runner.resume` 是阻塞式**（重放而非 live 流）：审批 resume 后 UI 在 run 完成前无实时输出。深层修复需让 resume 走 `start_streaming` 式 live 队列，属运行时改动，未做（方案 F3.2）。
3. `sanitize.py` 的 `_MAX_UNBROKEN` 截断（10k）目前只应用在 `table_output.emit`；TUI 主 transcript 走 `max_cells` 截断但未做单行截断（超大单行工具输出仍可能撑宽布局）。
4. shell completion（Typer `--install-completion`）被 `add_completion=False` 关闭，未启用。
5. `docs/cli-v2.md` 是用户向文档；`docs/cli-v2-baseline.md` 是升级前的基线清单。

---

## 10. 如何继续（给下一个 Agent）

- 先跑 `uv run pytest -q` 和 `uv run ruff check src apps tests` 确认基线。
- 想了解某个命令/模块：按 §3 文件地图 + `CLI-Upgrade-Plan/README.md` §1.2 路由。
- 做新任务：遵循 §2 执行协议；一次一 Task；改完跑测试 + ruff；每 Task 一 commit。
- **不要在 CLI 侧实现服务端权限语义**（审批 mode、auto-approve 都必须服务端支持）。
- 提交信息沿用 `cli-v2(Txx): ...` 或 `cli-v2: ...` 风格。

## 11. 提交历史（本分支）

```
2c9ca87 docs: add CLAUDE.md project memory — CLI v2 upgrade report & agent handoff
7f06ba6 cli-v2: fix adversarial-review findings (approval, injection, reliability)
8294dcc cli-v2: admin commands handle API/transport errors cleanly (no tracebacks)
60c4713 cli-v2(T50-T55): Phase 5 production hardening
97992c7 cli-v2(T40-T44): Phase 4 API projection improvements
fe40920 cli-v2(T30-T36): Phase 3 control-plane UX
0a1b515 cli-v2(T20-T26): Phase 2 TUI MVP (Textual)
afb2256 cli-v2(T10-T15): Phase 1 CLI Core
4105b3b cli-v2(T00-T03): Phase 0 baseline, packaging, legacy contract tests, SSE fixtures
```
