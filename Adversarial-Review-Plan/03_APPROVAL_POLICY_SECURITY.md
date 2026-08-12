# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## Approval / Policy 安全专项\n\n### 1. 统一 Approval 状态机\n建议状态：`pending -> approved|edited|rejected|expired|cancelled`，终态不可反转。DB 是唯一 authoritative state。不要 API 和 Engine 各自“补写”一次。\n\n### 2. 原子 resolve\n事务内：\n1. `SELECT ... FOR UPDATE` approval；\n2. 校验 owner、pending、expires_at；\n3. 计算 `bound_args`；\n4. `bound_hash = argument_hash(bound_args)`；\n5. 写 `arguments_preview=bound_args`, `argument_hash=bound_hash`, `status`, `approved_by`；\n6. 写 durable audit/outbox；\n7. commit；\n8. 返回 receipt。\n\n`approved_with_edits` 的 hash 必须是编辑后的参数。\n\n### 3. 幂等不能写成 except Exception: pass\n定义 `AlreadyResolved`，并携带现有 decision/hash/actor。只有请求与现有终态完全相同才返回现有 receipt；不同 decision 返回 409。Expired/DB error/ownership error 绝不能当成幂等成功。\n\n### 4. Runner 与 API 的职责\nAPI 做 owner authorization + command submission；Runner/ApprovalService 做原子状态转换。API 不直接修改 Approval 的 hash/status 来“兼容 fake”。测试 fake 应实现同一 protocol。\n\n### 5. Broker 执行前再验证\n保留 `verify_approval`，并增加：approval.run_id/session/owner 与 ToolExecutionContext 一致；tool_name 一致；hash 一致；status approved/edited；未过期。\n\n### 6. 修现有测试的 false positive\n`test_resume_with_edits_executes_edited_tool` 目前只验证 ToolCall.arguments 和 Run terminal，不足以证明 connector side effect 成功。新增可记录调用的 real connector，并断言：\n- `connector.execute_count == 1`\n- exact edited args\n- ToolCall.status == success\n- Approval.status == edited\n- persisted argument_hash == hash(edited_args)\n- final response 只在 ToolResult.success 后允许表达“已发送”。\n\n### 7. 审批后的恢复\n审批提交与 resume command 最好进入 durable queue/outbox，避免 API 把 run 改 running 后进程死亡。详见 `06_DURABILITY_CONCURRENCY.md`。\n