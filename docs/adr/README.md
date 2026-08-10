# Architecture Decision Records

| # | 决策 | 理由 | 何时重访 |
|---|------|------|---------|
| ADR-001 | LangGraph 作为 Agent Runtime | 需要 checkpoint/state machine/HITL/interrupt/provider-agnostic | 状态机逻辑与框架强耦合时 |
| ADR-002 | PostgreSQL + pgvector 统一存储 | 减少基础设施数量；MVP 用 SQLite 保证可移植，SQLAlchemy 抽象保持 PostgreSQL 兼容 | Memory 规模/检索吞吐超过 PG 范围时 |
| ADR-003 | Tool Broker 是唯一 capability execution path | 统一 auth/policy/approval/audit/retry/sandbox | — |
| ADR-004 | MCP 是 Tool protocol，不是 Runtime | 避免把 MCP 当 Agent Framework 造成架构混乱 | MCP 规范成熟并需 Tasks 时 |
| ADR-005 | 长期记忆必须有 provenance | 防止把模型猜测当事实，支持用户溯源/纠错/forget | — |
| ADR-006 | 第一版不引入 Temporal | LangGraph checkpointer + 自有 Run 持久化足够 | 出现多 worker/webhook waiting/长时 workflow 版本化 |
| ADR-007 | 第一版不做多租户/企业 RBAC | Single-operator first；所有核心对象保留 owner_id | 需要多用户时 |
