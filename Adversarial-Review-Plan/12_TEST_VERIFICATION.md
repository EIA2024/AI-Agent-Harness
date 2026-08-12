# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## 测试与验收策略\n\n### 为什么“461 tests green”仍能漏掉这些问题\n现有很多测试使用 scripted FakeProvider/FakeToolBroker。比如 tool-loop 测试第二个模型响应是脚本固定值，它不验证第二个 ModelRequest 是否真的包含 tool result；edited approval 测试也没有严格断言真实 connector 成功状态。因此测试可以绿而跨模块契约仍断。\n\n### P0 强制垂直测试矩阵\n| Case | 必须用真实模块 | 核心断言 |\n|---|---|---|\n| Tool result freshness | real ContextEngine + recording provider | 第二轮 request 包含 tool result |\n| Edited approval | real ApprovalEngine + real ToolBroker + recording connector | edited hash 持久化且 connector success exactly once |\n| HTTP mutate | real Registry+Policy+Broker | DELETE 未审批不执行 |\n| Restart approval | persistent DB+checkpointer | kill/restart 后 resume |\n| Multi-owner credential | real CredentialVault | B 不能用 A secret |\n| Reasoning privacy | provider fixture + API/SSE | raw reasoning 不出现在任何 public surface |\n| Deploy auth | compose/startup | default secret 不存在 |\n\n### Chaos / Concurrency\n- 20 concurrent POST same session\n- double approve/reject/edit\n- double resume\n- QueueFull + reconnect\n- DB transient failure around tool success\n- kill process at checkpoint boundaries\n- DNS rebinding mock\n- filesystem symlink cycle/regex bomb/100k entries\n\n### Security property tests\nCapability descriptor invariant、owner isolation、approval hash exact binding、trust unknown fail closed、secret sanitizer recursive。\n\n### CI 门禁升级\n保留 ruff/pytest；增加 migration test、coverage floor（先 baseline 再渐进）、dependency audit、secret scan、Bandit/Semgrep 规则、compose insecure config test、2-worker integration（当支持 multi-worker 后）。\n\n### Phase DoD\n每个 phase：目标 regression tests 绿；全量 tests 绿；ruff 绿；migration 可 upgrade；无新增 broad except/pass；文档/API event schema 同步；回滚 flag 明确。\n