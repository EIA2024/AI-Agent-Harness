# 002 — 对抗性代码审查修复：进度交接

> **主题**：`Adversarial-Review-Plan/`（DeepSeek 对抗审查）执行进度 + 剩余任务交接
> **日期**：2026-08-12
> **分支**：`cli-v2-upgrade`（本地工作树 `composed-giggling-ocean`）
> **上游文档**：`Adversarial-Review-Plan/README.md`、`ISSUE_REGISTER.csv`（47 项）、各 `0X_*.md`
> **接手 Agent 必读**：本文件 + `ISSUE_REGISTER.csv` + 对应专项 md

---

## 1. 总体进度

| 类 | 总数 | 已完成 | 剩余 |
|---|---|---|---|
| P0 | 8 | **8（全部）** | 0 |
| P1 | 34 | **24** | 10 |
| P2 | 5 | 2 | 3 |

**质量门禁**：520 个测试全绿，`ruff check src apps connectors tests` 干净。所有已完成项都带回归测试（多数验证了"改前失败/改后通过"）。

**重要**：服务端修复需**重启本地 API 服务**才生效（尤其是 P0-001 工具循环根因）。`uv sync` 因运行中的 `personal-ai.exe` 被 Windows 文件锁占用而无法重建 console script —— 依赖已用 `uv pip install` 直接装好，锁文件已更新；没有该进程时再跑 `uv sync` 即可。

---

## 2. 已完成修复（含位置，供复核/避免重复）

### P0（全部 8 个）

| 编号 | 修复位置 | 回归测试 |
|---|---|---|
| P0-001 | `agent_runtime/graph.py`：`observe/respond/plan/tool_request/approval/reflect/memory_commit` 返回 `_cached_context: None` | `tests/integration/test_tool_result_freshness.py` |
| P0-002 | `connectors/http_fetch/connector.py`（`http_fetch.fetch` 只读 + `http_request.mutate` R3）；`tool_broker/registry.py` 注册期 capability 校验 | `tests/unit/test_http_fetch.py`、`test_registry.py` |
| P0-003 | `policy_engine/approval.py` `resolve()` 持久化编辑后 args+hash | `tests/unit/test_approval.py`、`tests/integration/cli/test_approval_real_engine.py` |
| P0-004 | `policy_engine/approval.py` 类型化异常；`apps/api/main.py` 异常处理器（404/409/410/422）；runner/API 只容忍 `ApprovalNotPendingError` | `test_approval.py`、`test_approval_real_engine.py` |
| P0-005 | `gateway/services.py` `_open_persistent_checkpointer`（SQLite）；`runner.py` 警告 InMemorySaver | `tests/integration/test_checkpointer.py` |
| P0-006 | `deploy/compose/docker-compose.yml`（无默认密钥、Postgres 不发布端口、API 绑 127.0.0.1）；`apps/api/main.py` `_warn_insecure_config` | `tests/integration/test_deploy_security.py` |
| P0-007 | `policy_engine/credentials.py` owner-scoped + 禁全局 env 回退 | `tests/unit/test_credentials.py` |
| P0-008 | `runner.py` `_strip_reasoning`（递归剥离 reasoning_content/thinking）；graph 不再 live 推 thinking.delta | `tests/integration/test_reasoning_leak.py` |

### P1（24 个）与 P2（2 个）

- **connector/filesystem**：P1-002/003/004/005（`connectors/filesystem/connector.py`：深度 clamp、字面量搜索+`asyncio.to_thread`+扫描预算、显式 root、敏感文件拓宽）、P1-006（打开后重校验包含性）
- **broker/registry**：P1-013（capability 不变量：destructive→R4、external write→R3、side effect→R2）、P1-014（`gateway/capabilities.py` CapabilityService 接入 broker+API）、P1-015（`validate_tool` 替换 `test_tool`）、P1-001（结果 data 大小预算）
- **runtime**：P1-016（`ModelStreamError`，首 token 后断不重试）、P1-030（tool.started 移出审批前）、P1-031（`ToolArgumentParseError`，非法参数不执行）、P1-032（intake 身份 fail-fast）、P1-028（resume 也 compaction）
- **model/API**：P1-017（`provider.py` `_disambiguate` 工具名碰撞）、P1-018（`config.py` `_validate_base_url` HTTPS）、P1-022（CORS 默认关）、P1-023（`/readyz`）、P1-024（prod 禁 SQLite 回退）、P1-026（请求体/schema 限制）、P1-034（automation.run 不先写 last_run_at）
- **trust**：P1-007（result trust fail-closed：unknown→untrusted_tool）、P1-008（`context_engine/engine.py`：memory 移出 system_prompt 改 DATA frame）、P1-012（`http_fetch` DNS rebinding 混合解析阻断）
- **P2**：P2-002（`common/utils.py` `LogSanitizer.sanitize_value` 递归+深度上限）、P2-004（runner cost.usd 未知=null 非 0）

---

## 3. 剩余任务（10 P1 + 3 P2）—— 逐个交接

> 每一项的**精确 required_fix / required_tests 见 `Adversarial-Review-Plan/ISSUE_REGISTER.csv` 对应行**。下面给出推荐切入点与依赖关系。

### 第一优先：先建 Alembic 基建（解锁 4 项）

**P1-010**（`SessionRouter` get-or-create SELECT→INSERT race；仓库无 Alembic）
- 文件：`apps/api/routers/sessions.py`（get-or-create 逻辑）、`personal_ai_os/db/`、`alembic.ini`（已有，但 `migrations/versions/` 为空）
- 修：建 baseline revision；`INSERT ... ON CONFLICT` / IntegrityError retry
- **P1-025**（无 Alembic versions，只有 create_all）
- 修：建 baseline revision + 每变更一个 migration；CI 空库 upgrade head / 上个 release upgrade / downgrade smoke
- **依赖**：先做这两个，P1-011/021/027/009 才安全

### 次优先（依赖 Alembic 或独立可做）

**P1-021**（API key 明文存 DB —— 最高安全价值）
- 文件：`apps/api/deps.py` `resolve_user`、`apps/api/main.py` `ensure_dev_owner`、`db/models.py User`、`tests/conftest.py` + 所有 `User(api_key=...)` fixture
- 修：存 `api_key_hash`（HMAC/Argon2）+ `key_prefix`；创建时只返回明文一次；resolve 按 hash 查（兼容旧明文）；rotate/revoke/last_used_at
- 风险：会动全部 auth fixture —— **必须**在 Alembic 迁移后做，并跑全套回归

**P1-027**（Run resume CAS 后 runner 崩溃 → 永久 stuck running）
- 文件：`apps/api/routers/runs.py`（resume CAS）、`runner.py`
- 修：execution lease：`status + lease_owner + lease_expires_at + attempt`；worker claim 后执行，过期可回收；或 transactional outbox/job queue
- 测试：触发 approval → 杀进程 → 重启 → approve/resume → 只执行一次并完成

**P1-011**（消息/工具去重靠进程内 dict，重启后可能重复持久化）
- 文件：`db/models.py`（Message/ToolCall）、`runner.py` `_persist_messages/_persist_tool_calls`
- 修：DB `unique(run_id, event_id)` / `ToolCall unique(run_id, idempotency_key) where not null`；upsert
- 依赖 Alembic

**P1-009**（同一 Session 并发多个 Run，active_run_id 被覆盖）
- 文件：`apps/api/routers/sessions.py`、`messages.py`
- 修：默认 serialized；DB CAS/partial unique 保证每 session 最多一个 active run；新消息排队或 409
- 依赖 Alembic

**P1-029**（plan 只是展示性结构，不约束 tool 决策）
- 文件：`agent_runtime/planner.py`、`graph.py` `decide`/`plan`/`route_after_decide`
- 修：PlanStep 加 expected_capability/preconditions/postconditions/status；decide 绑定 current_step 或显式 replan；偏离产 `plan.changed` 事件
- 可独立做（不依赖 DB）

**P1-019**（SSE live queue 满时静默丢事件）
- 文件：`agent_runtime/streams.py`（Queue maxsize 静默 drop）、`apps/api/routers/stream.py`
- 修：事件先写 durable EventLog 或至少关键事件 durable；每 run 单调 event_seq；SSE 支持 Last-Event-ID/replay；token delta 合并但发 gap/coalesced 标记
- 需要事件表（Alembic）或进程内降级

**P1-020**（in-process streams/EventBus 在 multi-worker 失效）
- 修：multi-worker 前引入 Redis Streams/Postgres event log/NATS；sticky session 只能临时缓解
- 属架构决策 —— **建议先写 ADR**，不仓促实现

**P1-033**（安全审计 fail-soft，可无声丢失）
- 文件：`apps/api/routers/audit.py`、`tool_broker/broker.py` 审计点、`db/models.py AuditEvent`
- 修：分级 —— 业务 telemetry fail-soft；security audit 对 R3/R4 用 transactional outbox，至少先 durable intent 再执行
- 依赖事件/任务基建

### P2 剩余 3 项

**P2-001**（本地 provider API key 明文 JSON）
- 文件：`model_gateway/config.py` `ProviderConfigStore`、`cli/commands/config.py`
- 修：优先 OS keyring/Secret Service/DPAPI；JSON 仅存 secret_ref；兼容迁移旧 profile
- 平台依赖（Windows DPAPI / macOS Keychain）—— 建议分平台实现

**P2-003**（SQLite 并发不适合多任务写）
- 已被 P1-024 部分覆盖（prod 禁 SQLite）。剩余：dev 开 WAL + busy_timeout
- 文件：`db/session.py` `configure()` —— 加 `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000`

**P2-005**（事件 schema_version 固定 1，缺服务端兼容策略/持久 event id）
- 文件：`apps/api/routers/stream.py`（envelope）、`cli/domain/normalizer.py`
- 现状：decoder/normalizer 已宽容（未知事件→notice、忽略 schema_version、SSE 已带 seq/event_id）
- 剩余：正式 version negotiation + event_id 持久化（需事件表）

---

## 4. 接手者验证基线

```bash
cd .claude/worktrees/composed-giggling-ocean
uv run pytest -q            # 期望 520 passed
uv run ruff check src apps connectors tests
```

先跑通基线再改。改完每项：加回归测试 → 全套 pytest + ruff → 一个 commit（`fix(P1-xxx): ...`）。

**给接手 Agent 的硬性提醒**：
1. 不要在 CLI 侧实现服务端权限语义（审批 mode/auto-approve 必须服务端）。
2. 不要动 `apps/cli/main.py` 的 `_ensure_utf8()` 顺序（Windows GBK 问题）。
3. DB 演进必须走 Alembic（`migrations/versions/`），不要只依赖 `create_all`。
4. P1-021 动 auth 前先建 Alembic baseline，否则会破坏现有 520 绿基线。
5. 服务端改动需要重启本地 API 验证；`uv sync` 若因 `personal-ai.exe` 锁失败，用 `uv pip install`。
