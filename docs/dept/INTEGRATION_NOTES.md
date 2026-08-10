# 集成审查笔记 — 合并阶段发现并修复的问题

> 由主 Agent 在合并各子 Agent 分支时对抗性审查发现。按严重度排序。

## 已修复

### FIX-1: 审批决策字符串不匹配 + 重复 resolve（严重）
- **现象**：API `resume_run` 调用 `approval_engine.resolve(decision="approve")`，但 ApprovalEngine 期望 `"approved"`/`"rejected"`/`"approved_with_edits"`；且 API 自行 resolve 又不把 receipt 传给 Runner。
- **修复**：API 删除重复 resolve，统一委托 `runner.resume(run_id, approval_id, decision, edited_arguments)`；新增 `_normalize_approval_decision()`。审批生命周期由 Runner 持有。

### FIX-2: ServiceContainer 占位类名与真实实现不符（严重）
- **现象**：`build_default_services` 引用 `Runner`/`InMemoryEventBus`/`ModelClient` 等占位名，全部 import 失败 → 所有服务降级为 None。
- **修复**：重写为真实依赖图 + 补包级导出；无 API Key 时回退 EchoProvider（demo 模式）。

### FIX-3: 审批链路双重执行 + verify 未落地（严重，安全）
- **现象**：真实 ToolBroker + 审批流程：批准后重新执行会再次触发 policy "ask" → 无限审批环；且 ApprovalEngine 计算的 argument_hash 从未在 broker 边界被校验。
- **修复**：`ToolBroker` 注入 `approval_engine`；`execute()` 在 policy=="ask" 且 ctx.approval_id 存在时调用 `verify_approval(id, tool, args)` 校验精确参数 hash，通过才执行，不通过返回 POLICY_DENIED。新增 `ApprovalEngine.verify_approval()`。

### FIX-4: 工具调用被记录两次（数据一致性）
- **现象**：broker 的 `_record` 与 runner 的 `_persist_tool_calls` 各写一条 ToolCall 行 → 每个工具调用两条记录。
- **修复**：graph 序列化工具结果时携带 `idempotency_key`；runner 按 `approval_id` 或 `idempotency_key` 找到 broker 已写的行进行更新/跳过，不新建；broker 审批场景更新 awaiting_approval 行。broker 的 "denied/error" 丰富状态被保留。

### FIX-5: 审批预览参数解析 bug（安全，hash 绑定失效）
- **现象**：`_handle_approval_interrupt` 用 `json.loads(arguments)` 解析工具参数，但模型可能直接返回 dict → 解析失败 → preview 存成 `{}` → verify 失败。
- **修复**：兼容 dict/str 两种格式（与 `_extract_tool_call_fields` 对齐）。

### FIX-6: PostgreSQL 循环外键 + naive/aware datetime（跨方言）
- **现象**：`runs.session_id ↔ sessions.active_run_id` 循环 FK 使 PG 无法建/删表；PG 返回 aware datetime 而 SQLite 返回 naive，`recency_factor`/审批过期比较在 PG 上 TypeError。
- **修复**：`sessions.active_run_id` 去掉 FK 约束（保留列+索引）；`common/utils.py` 新增 `utc_now()`/`ensure_aware()`，记忆排序与审批过期比较统一使用。

### FIX-7: 默认服务接线 + API 契约失配（真实服务器冒烟测试发现）
- **现象**：`build_default_services` 未注册连接器工具（registry 空 → 无工具可用）；tools 路由调 `list_tools()`（实际是 `list_all()`）；runner 返回 `id` 而 API 期望 `run_id`。
- **修复**：新增 async `complete_wiring()`（注册连接器 + 构建 graph/runner），在 app lifespan 调用；tools 路由兼容 `list_all`；`get_run` 同时返回 `id`/`run_id`。

## 待合并 tools 后验证

### OPEN-1: 审批参数 hash 与预览脱敏
ToolBroker 创建审批时必须以**真实参数**计算 `argument_hash`（供执行前 verify），而展示给用户的 `arguments_preview` 需脱敏（`_secrets` 不得出现在预览中）。需检查 tools 子 Agent 的 broker 是否分离了这两者。

### OPEN-2: `_secrets` 剥离
CredentialBroker.inject 把 `_secrets` 塞进 arguments；连接器执行前必须剥离 `_secrets`（不能让连接器把 secret 当参数执行或回显）。需确认 ToolBroker 是否 pop。

### OPEN-3: 事件流 `tools` 接线
Tools 合并后 `build_default_services` 的 `_tool_broker` 应能成功注册 `get_builtin_connectors()`。

## 各子 Agent 自报的高质量修复（保留）

- Runtime：审批中断的 LangGraph replay 安全（专门 `approval` 节点，interrupt 前置）；messages/runs FK 循环顺序；EventBus 兼容 sync handler。
- mpm：策略双层匹配（specific 规则覆盖 risk-band）；审批 hash 绑定；`_secrets` 信封 + 脱敏。
- API：ASGITransport 单事件循环测试；lifespan 不覆盖测试 DB。
