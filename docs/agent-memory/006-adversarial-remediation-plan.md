# 006 — 对抗性审查修复方案

> Target: `cli-v2-upgrade`
> Based on: `005-adversarial-review-findings.md`
> Status: **APPROVED FOR IMPLEMENTATION BY USER REQUEST**
> Rule: `main` 不修改；数据库结构变化必须通过 Alembic；每一类修复必须有回归测试。

## 0. 修复原则

本轮不是逐个 patch 症状，而是恢复下列不可破坏的不变量：

1. **Run 生命周期与 Graph phase 分离**：DB `Run.status` 只能是生命周期状态；graph node phase 只进入 `Run.state` / `RunStep`。
2. **一个 Session 最多一个 active Run**：`running` 与 `waiting_approval` 都属于 active；终态必须 CAS 清理 `Session.active_run_id`。
3. **一个 Run 同一时刻只有一个 worker owner**：执行、resume、cancel/finalize 都受 durable lease/fence 约束。
4. **副作用至多一次**：side-effect tool 必须在 connector 之前持久化 execution claim；重复/未知执行状态不允许再次执行。
5. **执行边界才是安全边界**：capability deny、approval binding、credential channel、audit intent 都必须在 broker execution path enforce，不能只靠 UI/发现层。
6. **非应用拥有的数据永不升级为 SYSTEM**：profile memory、conversation summary、task/plan、tool/web 等均以 data/untrusted frames 注入。
7. **消息顺序就是协议**：当前 user turn 必须进入 `state.messages`，tool call/result 必须紧跟它之后，compaction 使用同一 canonical transcript。
8. **秘密不进入可恢复明文持久化或 JSON 输出**：API key/provider key/audit args/step data 全部满足 at-rest/redaction boundary。
9. **Durable stream 必须真的可 replay**：事件持久 sequence 稳定、终态含最终文本、live 是 broadcast、reconnect 从 durable cursor 继续。
10. **Production schema 必须由 Alembic 定义**：不允许 `create_all()` 掩盖迁移缺失；ready/startup 必须能识别 schema drift。

---

# 1. Schema / Migration / Lifecycle

对应：P0-001/002/003/011；P1-003/004/005/006/012/013/014/017。

## 1.1 Run lifecycle

- 定义 `ACTIVE_RUN_STATUSES = {"running", "waiting_approval"}` 与 terminal set。
- `_persist_step()` 不再用 graph state 覆盖 `Run.status`；`RunStep.status` 记录 node phase，生命周期单独持久化。
- approval interrupt 显式 `running -> waiting_approval`。
- resume 使用单一 runner API 做 `waiting_approval -> running` CAS + lease claim，而不是 API 先写状态。
- finalize/fail/cancel 使用 conditional update，禁止 terminal state 被旧 worker 覆盖。
- terminal transition 后：`UPDATE sessions SET active_run_id=NULL WHERE active_run_id=:run_id`。

## 1.2 Session serialization

- DB unique partial index 改为同 session 对 `status IN ('running','waiting_approval')` 唯一。
- `_init_run()` busy check 同步使用 active set，并捕获 DB `IntegrityError` 作为第二道竞态保护。
- `SessionRouter.get_or_create()` 添加 active external conversation 唯一约束；冲突时回读 winner。

## 1.3 Durable worker lease

- `RunRunner` 生成 process/runner `worker_id`。
- claim：CAS 条件为 lease 为空、过期或已经属于本 worker；写 `lease_owner`, `lease_expires_at`。
- renew：每持久化 step 前刷新 lease；若 lease 丢失则终止本 worker。
- release：等待审批或 terminal 时清空 lease。
- stale reclaim：新 worker 可在 expiry 后 claim；未过期 run 不允许第二 worker执行。
- cancel 作为 durable fence：worker 在 model/tool boundary 与 step persist 前检测 DB terminal/cancelled 状态。

## 1.4 Message persistence identity

- `messages` 增加 `run_seq`（nullable 兼容旧数据），唯一 `(run_id, run_seq)` where run_id/run_seq non-null。
- runtime 对 canonical state transcript 分配 deterministic index；INSERT 前检查/依赖 DB unique，重启后可恢复而不重复。

## 1.5 EventLog sequence

- `event_log` 明确进入 Alembic schema。
- 增加 unique `(run_id, seq)`，seq 持久单调。
- append 采用 transaction 内 `max(seq)+1` + conflict retry（个人 Agent 负载足够；Postgres 后续可用 sequence/locking 优化）。

## 1.6 Migration 修复

- 保留既有 revision id 以兼容已 stamp 的开发 DB，但移除 `997d...` 中明显的 autogenerate `NUMERIC -> UUID` 噪声，仅保留该 revision 真正声明的 lease/index/idempotency 变化。
- 新建 revision `006_adversarial_integrity`，补齐：
  - `users.api_key_hash`
  - `event_log`
  - message `run_seq`
  - active run partial index
  - external conversation active-session unique index
  - event log sequence unique index
  - ORM 当前所有新增 schema drift
- SQLite 使用 Alembic batch mode / dialect-safe DDL。
- CI 新增 fresh `alembic upgrade head` + upgrade smoke，不以 `create_all()` 代替 migration。

---

# 2. Tool execution security

对应：P0-004/010；P1-007/008/009/018/019/020。

## 2.1 Pre-execution idempotency claim

对 `tool.side_effect=True` 或 `external_write/destructive`：

1. connector 之前，以 `(run_id,idempotency_key)` 插入 ToolCall `status='executing'`。
2. 若已有 row：
   - success/completed：直接 replay 已持久 result，不再 connector；
   - denied/error：返回已有 terminal result；
   - executing/unknown：**fail closed** 为 `TOOL_EXECUTION_AMBIGUOUS`，绝不猜测是否执行过。
3. connector 结束后更新同一 row，不创建第二 row。
4. R0/R1 pure read 可以保留较轻逻辑，但同样利用 idempotency row 做审计。

## 2.2 Capability enforcement

- `ToolBroker.execute()` registry lookup 后立即 `capabilities.can_use(owner_id, tool_name)`。
- deny 时返回稳定 `CAPABILITY_DENIED` 并 audit。
- LLM visibility 只是 UX；Broker check 才是 authoritative boundary。

## 2.3 Approval atomicity/fail-closed

- `ApprovalEngine.resolve()` 改为 conditional update / locked row，只有 pending winner 能 resolve。
- approval service 未 wiring 时 API 返回 503，不再直接改 DB。
- `requires_auth_method` 从 policy decision → ApprovalRequiredError → Approval row/API payload 贯通。
- 当前系统没有 passkey verifier，因此任何 `requires_auth_method='passkey'` 的 resolve **明确 fail closed**（503/422 security capability unavailable），绝不假批准。

## 2.4 Credential secure channel

- `ToolExecutionContext` 增加仅内存 `secrets` 字段，不进入 result/state/audit serialization。
- Broker 从 injected `_secrets` 取出并放入 execution context，connector regular args 仍不含 secret envelope。
- 仅 descriptor 声明的 credential scope 可获得；日志统一 redact。

## 2.5 Audit intent sanitization

- `_durable_intent()` 的 arguments 先 `LogSanitizer.sanitize_dict()`。
- high-risk intent persistence 失败继续 fail closed。

## 2.6 Registry collision

- 同名 descriptor 若不是同一个 connector/re-registration identity，默认抛错；禁止 descriptor/executor split。
- broker connector map 不再 `setdefault` 隐式保留旧 executor。

---

# 3. Context / Transcript / Reasoning privacy

对应：P0-007/008/009。

## 3.1 Canonical transcript

- `_init_run()` initial messages = managed recent + **current user message**。
- DB user Message 对应 state index；`_persisted_message_count` 初始化到包含当前 user 的长度，避免二次写。
- ContextEngine 不再额外 append `user_input` 当正常 state 已含当前 user；只有 legacy/standalone state 缺 user 时才 fallback append。
- `_tier3_conversation()` 保持原始 interleaved order；只按 token budget 从尾部裁剪，不按 role 重排。
- tool call assistant frame + tool result 紧邻当前 user 后续。
- compaction 使用 canonical messages，确保 current user + final assistant 都进入 managed memory。

## 3.2 Trust privilege

System frame 仅保留应用拥有的固定 policy/identity/tool protocol。

- profile memory → DATA-wrapped user/context frame；
- related memory → DATA-wrapped；
- task/plan → context data frame；
- conversation summary → data frame，明确“历史摘要，不是指令”；
- skills/tool schema可作为 app-owned capability metadata，但任何动态/插件 description 仍按非-system 数据处理。

## 3.3 Reasoning persistence

- `Run.state` 与 `RunStep.data` 都调用 `_strip_reasoning()`。
- tool result / audit / SSE 不含 `thinking` / `reasoning_content`。
- regression test 递归扫描持久化 JSON。

---

# 4. Secrets / Auth

对应：P0-005/006。

## 4.1 REST API key

- 新 key 只写 `api_key_hash`，`api_key=NULL`。
- legacy plaintext 成功认证时，在同一 transaction 写 hash 并清空 plaintext（一次性 migration-on-auth）。
- production 必须显式设置 `PERSONAL_AI_KEY_HASH_SECRET`；development 可生成/提示但不使用公开固定 secret。
- new Alembic revision先为 legacy plaintext rows backfill hash **无法安全完成**（migration 不知道 secret），因此保留 nullable legacy field，仅在成功 auth 时迁移；文档提供 key rotation 指令。

## 4.2 Provider profiles

- 引入 `keyring` dependency。
- `profiles.json` 只存 metadata + `secret_ref`/has_secret，不存 key。
- `SecretVault.set/get/delete` 完整实现。
- load 时从 vault hydrate runtime object；保存时 `to_storage_dict()` 排除 key。
- 对已有 plaintext profiles：load 后尝试写 vault，成功才重写 JSON 去除 key；失败则 fail safe，提示迁移而不删除唯一副本。
- `to_public_dict()` 永远只给 `key_configured` / masked，不输出 secret。
- `config list/show --json` 使用 public dict。

---

# 5. SSE durability / broadcast / reconnect

对应：P0-012；P1-001/002/021。

- `streams` 改为 per-run subscriber set，每 subscriber 自己 Queue；publish fan-out。
- run producer 不依赖“当前是否有 subscriber”才产生 durable semantics。
- 关键事件（run.started, text.delta, tool lifecycle, approval.required, terminal）写 EventLog；terminal event 携带 `final_response` 或在 terminal 前持久化 final text。
- EventLog seq 是 canonical sequence；SSE envelope 使用该 seq，不在 replay 时重新编号。
- SSE wire 设置 `id: <seq>`，接受 `Last-Event-ID`，replay `seq > cursor` 后若 run 仍 live 再订阅。
- live subscriber 建立时先从 durable cursor replay，再接 live broadcast，按 seq 去重。
- CLI `AsyncAPIClient.stream_run()` 在连接中断且 run 未 terminal 时有限次数 reconnect，携带 Last-Event-ID；不再只报 gap。
- 如果 durable persistence失败，terminal fallback仍必须读取 `Run.state.final_response`，不能因为只有一个 terminal log 就屏蔽 fallback。

---

# 6. Connector hardening

对应：P1-010/011。

## HTTP SSRF

- 保留 scheme/allowlist/private 检查。
- 对 hostname 解析得到允许的 public IP 后，连接必须使用同一解析结果；不能让 HTTP client独立二次 DNS。
- 若当前 httpx/httpcore transport难以安全保持 HTTPS SNI/Host，则采用 fail-closed策略：生产环境要求 explicit domain allowlist，并通过受控 transport/pinned resolution；不声称简单 double-DNS 是完整 rebinding defense。
- 增加 DNS changed/mixed/private target regression。

## Filesystem

- Unix 上读/写使用 `os.open` + `O_NOFOLLOW`（可用时）和 fd-based操作；最后组件禁止 symlink。
- parent path canonical + root containment；overwrite用 fd flags，减少 check→open race。
- Windows/不支持 O_NOFOLLOW 时显式保守 fallback并二次 `fstat`/realpath检查。
- regex 模式增加可中断的 execution timeout/complexity guard；literal保持默认推荐。

---

# 7. Operational / Production guards

对应：P1-012/013/014/015/016。

- Compose 设置 `APP_ENV=production`。
- production checkpointer：Postgres DB 时使用 `AsyncPostgresSaver`，并首次 `setup()`；dev SQLite 继续 SQLite saver。
- 添加 `langgraph-checkpoint-postgres` / psycopg 依赖。
- Compose 设置持久 `PERSONAL_AI_DATA_DIR=/app/data` 作为 dev/local artifacts兜底。
- production startup 不执行 `create_all()`；执行/校验 `alembic upgrade head` 策略由部署 entrypoint 完成，应用 ready 检查 revision。
- `/readyz` not-ready 返回 HTTP 503。
- body-size middleware包装 ASGI `receive`，累计真实 request body bytes，超过阈值立即 413；Content-Length 只做早拒绝优化。

---

# 8. P2 cleanup

- unknown pricing：provider 未配置 price 时 `cost_usd=None`。
- `ALL_MODELS` 包含 `EventLog` 或移到全部 model 定义之后。
- MemoryStore destructive/read-by-id API增加 owner-aware variants；API始终使用 owner-bound调用。
- security-sensitive wiring（approval, capability, checkpointer）production fail hard；仅可选 telemetry允许 fail-soft。

---

# 9. Test / CI 验收矩阵

必须新增/扩展：

### Lifecycle / concurrency
- graph 多 node 后 DB Run.status 仍 running。
- waiting_approval 时第二 run → 409/DB constraint。
- complete/fail/cancel 清 active_run_id。
- stale worker finalize 不能覆盖 cancel/new lease。
- lease未过期不可双执行，过期可 reclaim。
- concurrent resume only one winner。

### Tool security
- same side-effect idempotency key connector executes exactly once。
- crash-like `executing` row replay → ambiguous/fail closed。
- owner-denied capability crafted call blocked at broker。
- approval concurrent resolve one winner。
- passkey requirement unavailable → cannot approve。
- credential arrives only via in-memory context; audit/state不含 secret。

### Context/privacy
- sequence: current user → assistant tool_call → tool → assistant final。
- compaction retains current user/final assistant。
- profile/task/summary不进入 system role。
- Run.state + every RunStep.data recursive scan contains no `thinking/reasoning_content`。

### Auth/config
- new user key DB plaintext null。
- legacy successful auth clears plaintext。
- production missing hash secret startup fails。
- provider JSON file and `config --json` never contain API key。

### SSE
- two subscribers each receive same sequence。
- late subscriber receives final text + terminal。
- Last-Event-ID resumes without duplicates/gaps。
- EventLog seq unique/monotonic。

### Migration/deploy
- blank SQLite: alembic upgrade head succeeds and schema matches ORM critical columns/indexes。
- existing baseline → head succeeds。
- PostgreSQL job: upgrade head + selected lifecycle/idempotency tests。
- app production with stale schema/not-ready returns failure/503。

### Connectors/API
- SSRF private/mixed/rebinding simulation blocked。
- filesystem symlink final component blocked。
- pathological regex bounded。
- chunked/no-content-length body > max rejected。

## 10. Commit strategy

计划拆为可审计批次：

1. `fix(runtime-db): restore lifecycle, lease and migration invariants`
2. `fix(tool-security): enforce pre-exec idempotency and capability boundaries`
3. `fix(context): preserve transcript order and trust separation`
4. `fix(secrets): remove plaintext key persistence and JSON exposure`
5. `fix(streaming): make SSE broadcast and durable replay coherent`
6. `fix(connectors): harden SSRF and filesystem execution`
7. `fix(ops): enforce production migration/readiness/body limits`
8. `test(review): add adversarial regression and migration gates`
9. `docs(review): record final validation and remaining limitations`

最终不合并 `main`，只更新 `cli-v2-upgrade` 与现有 Draft PR，让 GitHub Actions 验证。