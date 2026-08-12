# 004 — ReAct 工具循环收敛修复

> 日期：2026-08-12  
> 分支：`cli-v2-upgrade`  
> 前置交接：`docs/agent-memory/003-loop-bug-handoff.md`  
> 目标：解决模型在工具成功后仍重复调用、忽略 `TOOL_REPEAT_BLOCKED`，最终靠 `MAX_TOOL_CALLS=5` 被动退出并误称“无法调用工具”的问题。

---

## 1. 新发现的结构性问题

`003` 已经修复了三类根因：

1. `observe` 后缓存上下文陈旧，模型看不到最新工具结果；
2. 完全相同的成功调用会被 `TOOL_REPEAT_BLOCKED` 拦截，connector 不再二次执行；
3. 长工具循环会把原始用户指令挤出固定消息窗口。

继续审查后确认还有两个确定性问题：

### 1.1 `TOOL_REPEAT_BLOCKED` 同时消耗全局 5 次工具预算

旧 `decide()` 使用：

```python
len(state.get("tool_results") or []) >= MAX_TOOL_CALLS
```

但 `tool_request()` 在发现重复成功调用时，会向 `tool_results` 追加一个 `TOOL_REPEAT_BLOCKED` 伪结果；此时 connector 实际没有执行。

因此防重复机制本身会加速 `MAX_TOOL_CALLS=5` 耗尽。更重要的是，全局 5 次上限本应作为最终保险，而不是正常的收敛机制。

### 1.2 被拦截的伪调用仍经过 `execute`，错误推进 Plan

旧路由无论 connector 是否真正执行，只要不是审批状态都会走：

`tool_request -> execute -> observe`

而 `execute()` 会把当前 plan step 标记为 `done` 并推进 `current_step`。因此一次 `TOOL_REPEAT_BLOCKED`（以及参数 JSON 解析失败）也会被记录成“完成了一个执行步骤”。

---

## 2. 已实施修复

### 2.1 预置系统级工具收敛规则

提交：`7a369568` — `fix(runtime): preempt repetitive tool loops in system prompt`

文件：`src/personal_ai_os/context_engine/prompts/identity.md`

新增长期不变量：

- 每个新工具调用必须产生回答用户所必需的新信息；
- 同一工具 + 同一参数成功后不得重放；
- `TOOL_REPEAT_BLOCKED` 表示已有等价成功结果，不表示工具不可用；
- 工具成功后默认下一步是综合结果并回答；
- 禁止“再确认一次 / 换个近似 URL / 再列一次目录”式无信息增益调用；
- 不得把截断结果或重复拦截描述成“无法调用工具”；
- 多次尝试无新信息时应停止工具探索并基于已有结果回答。

这样模型在第一次工具调用前就知道收敛契约，不再只依赖错误发生后的提示。

### 2.2 Runtime 主动收回控制权

提交：`54b8a41e` — `fix(runtime): reclaim control from repetitive tool loops`

文件：`src/personal_ai_os/agent_runtime/graph.py`

核心改动：

1. `MAX_TOOL_CALLS=5` 保留，作为全局保险；
2. `TOOL_REPEAT_BLOCKED` 不再计入该全局预算，因为 connector 并未执行；
3. 新增 `MAX_CONSECUTIVE_REPEAT_BLOCKS=2`：
   - 第一次重复被拦截后，允许模型有一次机会改成真正产生新信息的调用；
   - 若连续第二次仍无视重复拦截，下一次 `decide` 直接不再提供 tools；
   - 即使 provider 仍返回陈旧 tool call，runtime 也会清除该 tool call 并进入 respond；
4. 每次已有成功工具结果时，`decide` 都动态追加一个很短的系统级收敛提示，明确要求优先回答、只有缺少必要信息时才能继续调用；
5. 两次重复后强制总结的系统提示明确写出：不得将重复拦截描述为“工具不可用”；
6. 新增轻量诊断状态：
   - `tool_repeat_blocked_count`
   - `tool_loop_guard_reason`（`repeat_blocked` / `max_tool_calls`）
7. 新增日志，记录 runtime 为什么回收工具控制权。

### 2.3 伪调用不再推进 Plan

同一提交 `54b8a41e` 中：

- `TOOL_REPEAT_BLOCKED`
- `TOOL_ARGUMENT_PARSE_ERROR`

都会从 `tool_request` 直接路由到 `observe`，跳过 `execute`。

因此只有真正执行 connector 的工具调用才会推进 `current_step` / 将 plan step 标记为 `done`。

---

## 3. 新增回归测试

提交：`ac74fd11` — `test(runtime): cover tool-loop convergence guards`

文件：`tests/unit/test_tool_loop_convergence.py`

覆盖：

1. repeat-block 伪结果不会消耗全局工具预算；
2. 第一次重复拦截后，模型仍可以使用实质不同参数进行一次合理恢复调用；
3. 连续两次重复拦截后，在达到 5 次全局上限前就强制停止工具调用；
4. stubborn provider 即便在 tools 已移除时仍返回 tool call，runtime 也会忽略；
5. repeat-block 不推进 L2 plan；
6. 5 次真实工具回合的全局保险仍然有效。

---

## 4. 验证状态

### 已完成

- 代码和测试已提交到 `cli-v2-upgrade`；
- `main` 未修改；
- 创建 Draft PR `#1`：`fix(runtime): make ReAct tool loops converge`，仅用于触发仓库现有 GitHub Actions；
- PR 不得在本任务中 merge；
- GitHub Actions `CI` 已验证本分支最终代码：
  - Python 3.12：`ruff` 成功，完整 `pytest` 步骤成功；
  - Python 3.13：`ruff` 成功，完整 `pytest` 步骤成功。

因此本次新增 convergence 代码、回归测试以及仓库既有测试均通过 CI 门禁。

### 执行环境说明

当前模型执行环境无法从容器直接 clone GitHub（DNS/network 不可用），因此没有使用本地 `uv run pytest -q`；验证改由仓库原生 GitHub Actions 完成。

仓库 CI 只监听：

- push 到 `main`
- 指向 `main` 的 pull request

因此使用 Draft PR 触发 CI，同时保持 main 不动。

---

## 5. 剩余：真实模型复现

自动化测试与静态检查已经通过。由于真实 API 服务不在当前可操作执行环境中，最后还需要在部署/本地服务上进行一次行为级复现：

1. 重启 API 服务，使 runtime/prompt 变更生效；
2. 重新跑：
   - “帮我看看原神现在最新版本是多少”
   - “帮我看看这个文件/目录”
3. 验收重点不是“能否撞到 5 次上限”，而是：
   - 首次成功结果后是否直接总结；
   - 如果模型第一次重复，connector 是否仍只执行一次；
   - 第二次连续重复后是否立即回收 tools；
   - 最终回答是否使用已有成功结果；
   - 不得出现“工具无法调用”的错误归因。

如果真实模型仍通过不断改变近似参数（例如不断换 URL）规避 exact-signature guard，再根据真实 run 证据增加“信息增益/同工具探索”诊断；不要预先把研究类任务粗暴限制成单次工具调用。

---

## 6. 不变量 / 注意事项

- 不要动 CLI `_ensure_utf8`；
- DB schema 如需改动必须走 Alembic（本次没有 DB schema 改动）；
- 服务端 runtime/prompt 改动后，真实复现前必须重启 API；
- `MAX_TOOL_CALLS=5` 仍只是最后保险，不应成为普通请求的正常退出路径；
- 不要为了减少循环而直接把所有工具请求限制成 1 次：研究/多步任务仍需要合理的不同参数调用；
- “连续 2 次 repeat-block”是刻意选择的折中：允许一次恢复机会，但不给模型无限违反同一约束的空间。
