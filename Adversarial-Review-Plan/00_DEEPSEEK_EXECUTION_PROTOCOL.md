# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## DeepSeek 执行协议

### 1. 每次只做一个 Task ID
每次开始先读取 `deepseek_tasks.json` 中当前 task，禁止跨 phase 顺手重构。每个 task 建一个独立 commit。

### 2. 修改前必须输出“证据摘要”
必须列：当前行为、触发路径、受影响函数、预计不变量、将新增的测试。若代码已被其他 commit 修复，先证明再把 task 标记 `already_fixed`，不要重复实现。

### 3. 先测试后改实现
P0/P1 的流程固定为：
1) 写一个在旧代码上失败的 regression test；2) 运行并记录失败；3) 最小实现；4) 单测；5) 相关集成测试；6) 全量 pytest/ruff。

### 4. 安全状态禁止 best-effort
Approval、credential、owner boundary、tool capability、audit intent 属于安全边界。安全边界发生未知异常必须 fail closed。

### 5. 不要相信 fake 的“完成”
工具审批测试必须同时断言：connector 被真正调用；收到的 exact args；ToolCall.status；Approval.status/hash；Run final state。仅断言 final_response='sent' 不算成功。

### 6. exactly-once 的真实定义
网络/进程系统无法仅靠内存 dict 保证 exactly-once。修复目标是：**业务 side effect 使用稳定 idempotency key + durable unique constraint + recoverable checkpoint**。

### 7. 每个 commit 的完成模板
- Task: `P0-xxx`
- Files changed
- New invariant
- Tests added
- Commands run + results
- Migration/compat impact
- Rollback path
- Remaining known risk

### 8. 失败时停止条件
如果某 task 需要改变公共 API/event schema/database schema，先完成兼容设计和 migration，再继续；不要直接破坏 CLI v2。
