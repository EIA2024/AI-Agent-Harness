# 集成审查笔记 — 合并阶段发现并修复的问题

> 由主 Agent 在合并各子 Agent 分支时对抗性审查发现。按严重度排序。

## 已修复

### FIX-1: 审批决策字符串不匹配 + 重复 resolve（严重）
- **现象**：API `resume_run` 调用 `approval_engine.resolve(decision="approve")`，但 ApprovalEngine 期望 `"approved"`/`"rejected"`/`"approved_with_edits"`，会抛 ValueError。
- **根因**：API 层与审批引擎对决策词汇的契约不一致；且 API 自行 resolve 审批、又不把 receipt 传给 Runner，导致"批准 A 执行 B"的 hash 绑定链路断裂。
- **修复**：`apps/api/routers/runs.py` — 删除 API 内重复 resolve；统一委托 `runner.resume(run_id, approval_id, decision, edited_arguments)`；新增 `_normalize_approval_decision()` 规范化客户端字符串。审批生命周期完全由 Runner 持有。

### FIX-2: ServiceContainer 占位类名与真实实现不符（严重）
- **现象**：`build_default_services` 引用 `Runner`/`InMemoryEventBus`/`ModelClient`/`MemoryStore`/`ContextEngine`/`SchedulerService`/`AuditLogger` 等占位名，全部 import 失败 → 所有服务降级为 None。
- **根因**：API 子 Agent 在兄弟模块不存在时用猜测的类名写接线代码。
- **修复**：`src/personal_ai_os/gateway/services.py` 重写为真实依赖图（EventBus→Memory/Audit、Policy→Broker、Memory→Context、Model→Runner），并为 `context_engine`/`scheduler`/`observability`/`agent_runtime` 补了包级导出。无 API Key 时回退 EchoProvider（demo 模式）。

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
