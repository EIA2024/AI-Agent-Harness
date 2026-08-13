# Agent Start Prompt — 直接交给 Coding Agent

你正在升级仓库 `EIA2024/AI-Agent-Harness` 的 CLI。

你的目标不是一次性重写，而是严格执行本目录中的 CLI v2 升级方案。

## 强制读取顺序

先读取：
1. `README.md`
2. `00_EXECUTION_PROTOCOL.md`
3. `01_CURRENT_STATE_AUDIT.md`
4. `17_IMPLEMENTATION_PHASES.md`
5. `18_AGENT_TASK_CARDS.md`
6. `19_ACCEPTANCE_CHECKLIST.md`

然后只执行我指定的 Task ID；如果我没有指定 Task ID，从 `T00` 开始。

## 执行约束

- 一次只执行一张 Task Card。
- 修改前先验证对应现有仓库文件的真实状态，不得假设方案中的路径永远与最新代码一致。
- 不得为了 UI 重写 Agent Runtime、Memory Engine、ToolBroker 或 Policy Engine。
- CLI 不得自行成为权限真源，不得通过自动 POST approve 模拟 YOLO。
- API/transport 层不得直接 print 或 `SystemExit`。
- Server SSE 必须先 normalise 成 UIEvent，再进入 reducer/widget/headless renderer。
- interactive 与 headless 必须共享 transport/domain，不共享 presentation。
- 默认不得向用户展示 raw chain-of-thought。
- 所有 terminal 输出必须防 ANSI/OSC/control-sequence injection。
- 保留旧命令兼容，除非 Task/ADR 明确要求 breaking change。
- 每个 Task 完成后必须运行该 Task 所需测试和仓库现有质量检查。
- 如果缺少服务端能力，标记 BLOCKED 并路由到 P1/P2 Task；不得在客户端伪造服务端语义。

## 每张 Task 完成后输出

```text
Task:
Status: PASS / PARTIAL / BLOCKED
Files changed:
Behavior changed:
Compatibility impact:
Security impact:
Tests:
Commands run:
Known limitations:
Recommended next Task:
```

现在开始执行指定 Task。
