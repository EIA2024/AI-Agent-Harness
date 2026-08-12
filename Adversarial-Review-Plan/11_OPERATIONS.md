# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## Observability / Operations / Automation 专项\n\n### Health\n`/livez` 仅进程；`/readyz` 检 DB、migration head、checkpointer、runner、provider、required connector、credential backend。不要 EchoProvider 悄悄算 ready。\n\n### Audit\nR3/R4 action 采用 durable audit intent/outbox；记录 `run_id/tool_call_id/approval_id/owner_id/arg_hash/result_hash/idempotency_key`，不记录 raw secret/reasoning。\n\n### Metrics\n至少：tool calls by outcome、approval latency、repeat signature count、checkpoint recovery count、queue gap/drop count、LLM retries、token/cost、DB payload sizes、event loop lag、filesystem scan budget hits。\n\n### Automation\n当前 run endpoint 在 scheduler 不存在时先写 last_run_at 再 501。改为 `last_attempt_at`, `last_success_at`, `last_error`。真正 scheduler 上线前 API 应明确 feature disabled，而不是看起来部分可用。\n\n### Degraded mode\ndev 可以 graceful degradation；production 用 feature matrix 明确哪些组件 optional。required component wiring failure 应 fail startup/readiness。\n