# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## Master Roadmap

### Phase 0 — 建立安全测试地基
目标：先让当前关键缺陷可重复。
- P0-001 tool result freshness regression
- P0-002 HTTP mutate policy regression
- P0-003 edited approval real broker regression
- P0-005 restart/resume harness
- P0-006 insecure compose static check
- P0-008 reasoning leakage test
**DoD**：这些测试在旧分支明确失败，且失败原因与问题证据一致。

### Phase 1 — 修 Agent Runtime 状态一致性
P0-001 → P1-001 → P1-016 → P1-029/030/031/032。
先让模型真正看到工具结果，再引入结构化 ResultEnvelope 和明确 lifecycle。不要先调 MAX_TOOL_CALLS。

### Phase 2 — 修审批/策略安全边界
P0-003 → P0-004 → P1-013 → P1-033。
必须把 Approval DB row、receipt、broker execution 的 hash 变成一个不可分裂协议。

### Phase 3 — 修 Connector Capability 与 Sandbox
P0-002 → P1-012 → P1-003/002/004/005/006。
HTTP read/mutate 分离；filesystem 从“路径字符串检查”升级为受资源预算约束的 workspace capability。

### Phase 4 — 持久化与并发
P0-005 → P1-011 → P1-027 → P1-009/010 → P1-019/020。
引入 persistent checkpointer、DB idempotency、run lease、session serialization、durable event seq。

### Phase 5 — 身份/多租户/部署
P0-006 → P0-007 → P1-021/022/014/023/024/025。
明确 single-user 与 multi-user mode；prod 不允许 dev secret/fallback。

### Phase 6 — Context/Memory/Reasoning Trust
P0-008 → P1-007/008 → P2-002。
建立 provenance/trust lattice，禁止 user-derived memory 被提升为 system instruction。

### Phase 7 — Model/CLI/Operations/Future
P1-017/018/019/020/034 + P2 系列；再实现 scheduler/artifact/MCP/multi-agent。

## 依赖图（关键）
`P0-001 -> P1-001`；`P0-003 -> P0-004 -> P1-033`；`P0-002 -> P1-013`；`P0-005 -> P1-011 -> P1-027`；`P0-007 -> P1-014`；`P0-008 -> P1-008`。
