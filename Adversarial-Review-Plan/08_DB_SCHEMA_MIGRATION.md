# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## DB / Schema / Migration 专项\n\n### 立即建立 Alembic baseline\n当前仓库只有 Alembic env/template，没有 versions。先基于当前 model 生成 baseline，手工审阅，再追加安全/并发约束。生产 startup 只检查 migration head，不再 `create_all()`。\n\n### 推荐新约束\n- sessions active external conversation partial unique\n- messages `(run_id,message_seq)` unique\n- tool_calls `(run_id,idempotency_key)` partial unique\n- run_events `(run_id,seq)` unique\n- approvals argument_hash NOT NULL（迁移旧 pending 要回填）\n- API key 拆 `key_prefix/key_hash`\n- run lease fields\n\n### Payload 限制\nHTTP body max（例如 1–4 MiB，按实际产品）；Message.text/Memory.content/Automation.prompt 设置合理字符上限；dict 限 nesting/items。ToolResult 大对象放 artifact/blob，不进 JSON column。\n\n### SQLite\n只作为 dev single process。开启 foreign_keys/WAL/busy_timeout 并不等于生产可用；文档必须写清楚。\n