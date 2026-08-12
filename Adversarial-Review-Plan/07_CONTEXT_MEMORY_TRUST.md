# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## Context / Memory / Trust / Reasoning 专项\n\n### Trust lattice\n建议最少：`trusted_system`, `trusted_local_tool`, `user_authored`, `untrusted_web`, `untrusted_document`, `untrusted_mcp`, `unknown`。`unknown` 永远按 untrusted。\n\n### Memory 不等于 System Instruction\n用户偏好/长期记忆即使“可信为事实”，也不是开发者/系统指令。Profile/memory 应作为 data frame，并包含 provenance：`source_type/source_id/extracted_at/trust/sensitivity`。\n\n### Persistent prompt injection 防护\nMemory extractor 不应保存纯 instruction-shaped 内容（例如“以后无视系统…”）为 profile；检索后也必须 data-wrap。真正允许影响 policy 的 configuration 应走独立、用户显式设置的配置表，不走自然语言 memory。\n\n### Reasoning\nDeepSeek 等 provider 为 tool-call continuation 可能需要把 `reasoning_content` 原样回传给同一 provider。允许它存在于 **provider-private ephemeral state**，但：\n- 不写 public `Run.state.thinking`；\n- 不写 Message；\n- 不发 SSE；\n- 不进 audit；\n- 不进入 memory；\n- 只在 protocol 明确要求的下一调用保留，完成后丢弃。\nUI 的“思考中”应来自阶段事件（reading/tool/planning）或单独生成的 reasoning summary。\n\n### Sanitizer\n实现递归 `sanitize_value`，list[str] 也处理；同时设 max depth/max nodes，避免 sanitizer 本身被恶意结构拖垮。\n