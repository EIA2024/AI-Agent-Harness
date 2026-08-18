# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> **历史状态（2026-08-17）**：本目录是针对上述旧 tree 的审查快照，不是
> `main` 的现役缺陷清单。修复与剩余风险的后续记录见
> [`docs/agent-memory/README.md`](../docs/agent-memory/README.md)，当前系统合同以
> [`README.md`](../README.md)、[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)
> 和测试为准。

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## 先看结论

当前分支不是“CLI 皮肤问题”，核心风险集中在 **Agent 状态一致性、审批安全、connector capability 元数据、重启恢复、多租户凭据、原始 reasoning 暴露、部署默认值**。

### 必须先修的 8 个 P0

1. **P0-001** stale `_cached_context`：工具返回虽然被写入 `state.messages`，下一轮 `decide()` 仍可能复用工具调用前 context，导致重复工具循环。
2. **P0-002** HTTP connector 把 DELETE/POST 等外部副作用伪装为 R1 read-only。
3. **P0-003** `approved_with_edits` 新参数 hash 没持久化，端到端实际仍可能被 broker 拒绝。
4. **P0-004** approval API/runner 对任意 engine 异常 `pass`，导致安全状态与 UI/执行状态分叉。
5. **P0-005** 生产默认 `InMemorySaver`，进程重启后 waiting approval checkpoint 丢失。
6. **P0-006** Compose 默认 `dev-key`、固定 DB 密码、数据库端口发布。
7. **P0-007** CredentialBroker 的 secret 是进程全局，不按 owner 隔离。
8. **P0-008** provider `reasoning_content` 通过 `thinking.delta` 持久化/重放。

## 文档阅读顺序

1. `00_DEEPSEEK_EXECUTION_PROTOCOL.md` — DeepSeek 必须遵守的执行纪律。
2. `01_MASTER_ROADMAP.md` — phase 与依赖关系。
3. `02_RUNTIME_TOOL_LOOP.md` ～ `11_OPERATIONS.md` — 专项修复。
4. `12_TEST_VERIFICATION.md` — 每阶段 DoD。
5. `14_ISSUE_REGISTER.md` / `ISSUE_REGISTER.csv` — 完整问题索引。
6. `15_DEEPSEEK_MASTER_PROMPT.md` + `deepseek_tasks.json` — 直接交给 DeepSeek。

## 禁止的修复方式

- 不允许简单把 `MAX_TOOL_CALLS=5` 改成更大数。它只是保险丝，不是工具循环根因。
- 不允许只继续扩大 `_preview_tool_data()`；必须先解决 context freshness，再解决 result pagination/budget。
- 不允许用更多 `except Exception: pass` 做“幂等”。
- 不允许只改单元测试 fake 让测试绿；P0 必须有真实模块垂直切片。
- 不允许把所有 connector 都粗暴升到 R4；应修复 capability 建模和 policy invariants。
- 不允许通过关闭审批/关闭多用户/关闭 SSE 来“绕过”问题，除非作为明确的临时 feature flag 回滚。

## 推荐发布门禁

P0 全部关闭 + P1-001/003/008/009/011/012/013/019/021/023/025/027/033 完成后，才建议把该分支作为长期个人服务器运行版本。
