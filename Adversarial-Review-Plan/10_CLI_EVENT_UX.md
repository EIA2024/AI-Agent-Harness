# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## CLI / SSE / UX 契约专项\n\n### 稳定事件身份\n当前 replay 重新生成 event_id 和 seq，无法做真正断点续传。事件产生时就赋 `event_id`, `run_seq`, `created_at` 并持久化。SSE 只是 transport。\n\n### Backpressure\n- token delta 可在 server 端 20–50ms 合并。\n- approval/tool/terminal 不得 drop。\n- 客户端发现 seq gap 必须触发 replay，而不是继续渲染。\n\n### Approval UI\n卡片必须区分 requested/approved/edited/rejected/expired；编辑后展示 diff + 重新计算 risk。若 edited args 提升风险（例如 read→delete），必须重新 policy evaluation，不能沿用原 approval。\n\n### “Thinking” UI\n改成 Progress：`planning`, `reading`, `calling_tool`, `waiting_approval`, `synthesizing`；不展示原始 reasoning_content。\n\n### Headless JSON/JSONL\n脚本接口中 terminal event 必须包含 stable status/error_code；unexpected EOF 必须非零退出；event schema 版本升级必须兼容 unknown fields/events。\n