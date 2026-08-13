# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## 可直接交给 DeepSeek 的 Master Prompt\n\n你正在修复 AI-Agent-Harness 的 `cli-v2-upgrade` 分支。不要一次性重构整个项目。首先读取本目录 `00_DEEPSEEK_EXECUTION_PROTOCOL.md`、`01_MASTER_ROADMAP.md`、`14_ISSUE_REGISTER.md` 和 `deepseek_tasks.json`。\n\n执行规则：\n1. 严格按 task 的 dependency 顺序，一次只处理一个 ID。\n2. 每个 VERIFIED task 必须先添加能在旧代码失败的测试。\n3. 安全边界（approval/policy/credential/owner/tool side effect/audit intent）未知异常 fail closed。\n4. 不要把 MAX_TOOL_CALLS 调大来修循环；不要继续只扩大 preview；不要用 `except Exception: pass`。\n5. 每个任务完成后输出：证据→修改→测试→兼容影响→回滚→仍存风险。\n6. P0 完成后先跑完整 pytest+ruff，再开始 P1。\n7. 如果仓库在执行期间已有新 commit，先 `git diff`/`git log` 判断 task 是否 already fixed；不要覆盖其他人的改动。\n8. 涉及 schema 时先写 Alembic migration；涉及公共 event/API 时保持 backwards compatible 或明确 version bump。\n\n从 **P0-001** 开始。其验收不是“最终回答正确”，而是第二轮真实 ModelRequest 明确包含刚产生的 tool result，并且 recording provider/connector 证明同一工具不会因 stale context 重复执行。\n