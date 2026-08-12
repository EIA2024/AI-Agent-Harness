# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## Durability / Concurrency 专项\n\n### Persistent Checkpoint\n`Run.state` 不是 LangGraph interrupt checkpoint 的等价替代。生产 graph 必须使用持久 checkpointer。启动时若 `APP_ENV=production` 且 checkpointer 是 InMemorySaver，应直接拒绝。\n\n### Durable Idempotency\n删除 `_persisted_message_count` / `_persisted_tool_ids` 作为正确性依据。可以保留为性能 cache，但 DB 必须有：\n- Message: stable `event_id` 或 `(run_id, message_seq)` unique\n- ToolCall: `(run_id,idempotency_key)` unique where key not null\n- RunStep/Event: `(run_id, sequence)` unique\n所有 insert 用 upsert/IntegrityError-as-duplicate。\n\n### Run Lease\n新增字段：`lease_owner`, `lease_expires_at`, `attempt`, `heartbeat_at`。worker claim waiting/runnable run；执行中续租；crash 后其他 worker reclaim。side-effect tool 仍靠 idempotency key 防重复。\n\n### Session Serialization\n默认同一 session 只有一个 active run。建议数据库部分唯一约束或独立 `SessionRunLock`。新消息可：A) 409；B) queued。不要默默并行。\n\n### SSE/EventLog\n当前 live queue 只可做优化，不能做 source of truth。关键事件先持久化 event_id/seq，再 fan-out。客户端 `Last-Event-ID` 重连；queue overflow 只允许 coalesce token delta，不能丢 tool/approval/terminal。\n\n### Restart Chaos Cases\n至少在以下时间 kill 进程：model streaming 中；tool requested 后；approval pending；approval DB commit 后 resume 前；connector 成功后 ToolCall commit 前；run completed 前。每个 case 定义恢复结果与 side-effect 次数。\n