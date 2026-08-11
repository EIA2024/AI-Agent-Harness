# Personal AI OS — Code Review 报告

> 最终 Code Review（Task #7）。覆盖：正确性、安全性、性能、代码质量、框架优化。
> 日期：2026-08-11

## 1. 审查范围与方法

- **静态检查**：ruff（E/F/W/I/UP 规则集），全部通过
- **对抗性审查**：独立子 Agent 攻击者视角扫描 + 主 Agent 逐模块自查
- **运行验证**：281 单元/集成测试、真实 HTTP 冒烟测试、PostgreSQL 全栈端到端
- **跨方言验证**：SQLite（测试）+ PostgreSQL（生产）双跑

## 2. 审查中修复的关键缺陷（按严重度）

| # | 严重度 | 问题 | 修复 |
|---|--------|------|------|
| 1 | CRITICAL | 审批"批准 A 执行 B"防护未在 broker 边界落地 | ToolBroker 注入审批引擎，执行前 `verify_approval` 校验精确参数 hash |
| 2 | CRITICAL | 审批批准后重新执行触发无限审批环 | 审批后 resume 携带 approval_id，broker 校验通过即执行 |
| 3 | HIGH | 同一工具调用被记录两次（broker + runner） | 以 idempotency_key/approval_id 去重 |
| 4 | HIGH | 审批参数 dict 形式解析失败 → hash 绑定失效 | 兼容 dict/JSON 字符串 |
| 5 | HIGH | PostgreSQL 循环外键（runs↔sessions） | 去掉 active_run_id 的 FK 约束 |
| 6 | HIGH | naive/aware datetime 跨方言崩溃 | `utc_now()`/`ensure_aware()` 统一规范化 |
| 7 | MEDIUM | 默认服务接线失配（连接器未注册/路由方法名错/字段不一致） | `complete_wiring()` + 修正路由与返回契约 |
| 8 | MEDIUM | 工具执行/拒绝未写审计日志 | AuditLogger 接入 ToolBroker（tool.executed/denied/mismatch_blocked） |

## 3. 已确认安全（对抗性验证通过）

- **Owner 隔离**：所有 API 路由按 `owner_id == user.id` 过滤（无 IDOR）
- **审批 hash 绑定**：`argument_hash` 精确绑定，改参数即拒绝（集成测试验证）
- **Calculator 注入**：`ast` 白名单，拒绝属性访问/dunder/字符串/非白名单调用
- **Filesystem 逃逸**：realpath + commonpath 校验，拒绝 `../`；敏感文件拒读
- **HTTP SSRF**：scheme 检查 + 域名 allowlist + IP/DNS 私有地址检测 + 禁重定向
- **Credential 隔离**：`_secrets` 信封在连接器执行前剥离；LogSanitizer 脱敏
- **Prompt Injection 隔离**：untrusted 内容以 DATA 标记包裹，不进入 system 消息
- **认证**：错误 X-API-Key → 401

## 4. 框架优化

1. **依赖倒置**：所有模块通过 `common/protocols.py` 的 Protocol 注入，无跨部门 import
2. **LangGraph replay 安全**：审批节点将 `interrupt()` 作为首个副作用语句
3. **单一写入者去重**：ToolCall 审计行以 idempotency_key 幂等
4. **fail-soft 原则**：事件发布/审计/策略引擎异常不阻断执行
5. **EchoProvider demo 模式**：无 API Key 时系统仍完整可运行
6. **StrEnum 现代化**、lint 全清

## 5. 遗留优化项（非阻塞，供后续迭代）

1. **Scheduler 执行**：automations CRUD 已实现，执行返回 501（需接 Scheduler）
2. **Memory embedding**：MVP 用确定性哈希向量；生产接真实 embedding model
3. **DNS rebinding**：http_fetch 的 SSRF 检查有 TOCTOU 窗口，生产需网络级 allowlist
4. **Web 前端**：apps/web 尚未实现（当前 API + CLI + SSE 可用）
5. **Redis/队列**：多 Worker/事件重放时引入 Redis Streams
6. **MCP / Skills / Sandbox / Browser**：设计完备，属 P1/P2 迭代

## 6. 结论

核心 Agent Runtime（Runtime + Context + Tool + Policy/Approval + Memory + Model + API）
在 SQLite 与 PostgreSQL 上均完整运行零错误，281 测试全绿，HTTP 冒烟与生产路径端到端通过。
安全边界（审批 hash 绑定、owner 隔离、prompt injection 隔离、SSRF、凭据隔离）经对抗性验证有效。
