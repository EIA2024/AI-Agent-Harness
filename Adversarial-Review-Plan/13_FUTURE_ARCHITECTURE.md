# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## Future Architecture 风险清单\n\n### MCP\n不要把 MCP tool 直接当 trusted_tool。每个 server 有 trust/capability/owner grants；schema 注册仍走 CapabilityValidator；MCP 返回默认为 untrusted_mcp；credential 不进入 prompt。\n\n### Scheduler / Background Agent\n必须建立 durable job queue + run lease + exactly-once-safe side effect，不能直接复用进程内 EventBus/asyncio task。通知属于外部副作用，走 R3 policy。\n\n### Artifact Browser\n大工具结果/文件 diff/HTTP body 应成为 artifact。Artifact 需要 owner ACL、content hash、size/type、retention、sensitivity、download authorization；不要让 DB JSON 无限增长。\n\n### Multi-agent\n子 Agent 不能继承父 Agent 全部 capabilities。采用 capability delegation token：明确 tool subset、budget、expiry、owner/run lineage。子 agent 的 memory 写入需 provenance。\n\n### Multi-worker / HA\n必须先替换 InMemorySaver、streams、EventBus correctness dependency。服务无共享 event/checkpoint 时禁止宣称 horizontal scalable。\n\n### Approval Modes（always ask / session allow / tool allow）\n未来“session allow”不能只存在 CLI state，server 要签发 scope-bound grant，绑定 owner/tool/risk ceiling/argument constraints/expiry，并在 Broker 验证。\n\n### Plan Mode\n只读 plan mode 必须在 server capability 层禁止 mutate tools，而不是依靠 prompt 说“不要执行”。\n\n### 版本兼容\nCLI 与 server 引入 `/capabilities` 或 version negotiation；event schema stable IDs；数据库 migration 与 API release 同步。\n