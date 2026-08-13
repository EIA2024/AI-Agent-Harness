# 00 — 执行 Agent 协议

## 目标

保证任何 Coding Agent 读取本包后，都以可验证、可回滚、低耦合方式改造仓库，而不是一次性大重写。

## 1. 强制规则

### R1. 先读后改
开始 Task Card 前：
1. 读取 Task Card 指定文件；
2. 读取它引用的现有仓库文件；
3. 写出“现状 -> 目标 -> 最小改动边界”；
4. 才能修改代码。

### R2. 一次只执行一个 Task Card
不要同时完成多个 Phase。每个 Task：
- 修改；
- 测试；
- 自查；
- 记录结果；
- 再进入下一 Task。

### R3. 不绕过后端安全边界
任何自动批准/工具执行/凭据行为都不得只在客户端实现。
- Policy Engine 是权限真源；
- CLI 是 UI，不是授权引擎；
- Approval edit 必须走现有 hash-binding / resume 机制。

### R4. 不把 API DTO 直接传播到 Widget
必须经过：
`HTTP/SSE DTO -> Normalizer -> UIEvent -> Reducer/AppState -> Widget`

### R5. 不在网络层 print
API client 只能：
- return value；
- yield event；
- raise typed exception。
所有输出由 renderer / TUI 负责。

### R6. 保留 headless
任何 TUI 设计都不能让 CI 依赖 TTY。

### R7. 每次改动保留 rollback point
建议每个 Task 一个 commit：
`cli-v2(Txx): <short description>`

---

## 2. 每个 Task 的执行模板

```markdown
# Task Txx Execution Note

## Inputs
- Read:
- Existing tests:
- API contracts:

## Intended change
- Files added:
- Files modified:
- Explicitly untouched:

## Risks
- Compatibility:
- Security:
- Streaming:
- Cross-platform:

## Implementation
...

## Verification
- unit:
- integration:
- manual:
- lint:

## Result
PASS / PARTIAL / BLOCKED

## Remaining
...
```

若 BLOCKED：
- 不伪造完成；
- 写明缺失 API 或契约；
- 路由到对应 P1/P2 Task；
- 保持当前分支可运行。

---

## 3. 允许的架构偏差

可以替换具体库，但必须写 ADR，且继续满足：
- async stream；
- composable widgets；
- testable rendering；
- headless independent；
- accessibility；
- Windows/Linux/macOS。

禁止因为“实现快”重新回到 1000+ 行 `main.py`。

---

## 4. 决策升级条件

遇到以下情况，停止当前 Task 并建 ADR：
- 要新增或改变服务端权限语义；
- 要暴露 raw chain-of-thought；
- 要改变 SSE 兼容契约；
- 要把 API key 明文写入 repo；
- 要删除旧命令；
- 要新增 shell / filesystem write 能力；
- 要将自动审批放到客户端；
- 要改变 Session 与 Project 的绑定语义。

---

## 5. 完成报告格式

执行完一个 Phase 后输出：

```text
Phase:
Tasks completed:
Files changed:
Public CLI changes:
API changes:
Security changes:
Tests added:
Commands to verify:
Known limitations:
Next recommended Task:
```
