# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## Model Gateway 专项\n\n### Streaming failure\n`_model_call` 只有在尚未向 runtime 提交任何 delta 时才允许 stream→complete fallback。首 delta 后失败应产生 `model.stream_interrupted`，由 UI 提示重试；如果自动 retry，需要 provider request id/idempotency 与用户可见语义。\n\n### Tool alias\n不要字符替换。给每个 tool 生成 provider-safe alias，例如 `t_<base32(hash(namespace+name))>_<short_name>`；维护 bijection，注册时检测 collision。历史 message frame 也使用同一 mapping version。\n\n### base_url / key binding\n外部 provider 强制 HTTPS；local provider 单独 `local_no_auth` profile。Unknown domain 首次配置时必须显示 key 将发送到哪个 host。\n\n### Cost\n未知价格不是 0，而是 `None/unknown`。预算层用 token count + provider pricing config；在 max cost guardrail 不可计算时采取显式策略。\n