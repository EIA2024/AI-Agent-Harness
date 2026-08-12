# 005 — 全分支对抗性审查：最终问题登记

> Branch: `cli-v2-upgrade`
> Initial reviewed HEAD: `fd24100ece9936a32e4c887126e4bb8279758b2e`
> Review completion point: after `e7756c021f6f6e46b651bdd17d9c8bb7aecd8fbe`
> Review mode: adversarial / fail-closed / concurrency + durability + security oriented
> Status: **REVIEW COMPLETE — REMEDIATION NOT YET APPLIED AT THIS COMMIT**

本文件按本轮要求先封板问题清单：先确认 bug 与影响，再另建 remediation plan，之后才修改业务代码。

## 审查范围

已覆盖：

- Agent Runtime / LangGraph state / checkpoint / resume / cancel / memory compaction
- Context Engine / trust boundary / prompt injection / tool-loop context ordering
- ToolBroker / Policy / Capability / Approval / Credential / idempotency
- HTTP / filesystem connectors
- Model provider/profile configuration
- REST API auth / run/session/message / SSE / readiness / body limit
- SQLAlchemy model / Alembic migrations / SQLite/PostgreSQL portability
- CLI SSE / config / secret rendering
- Docker/Compose deployment defaults
- CI gates / migration and concurrency test gaps

## 严重度

- `P0`: 破坏核心安全/执行边界、可重复副作用、严重生命周期/一致性错误、秘密直接泄露，或核心对话语义失效。
- `P1`: 高概率用户可见错误、恢复/并发/部署失败、较强安全风险。
- `P2`: 中低影响、可维护性或未来扩展风险。

---

# P0 — 必须优先修复

## AR2-P0-001 — `Run.status` 被 Graph phase 覆盖，活动 Run 约束几乎立即失效

- `Run` 创建时为 `running`，但 `_persist_step()` 每个 node 都执行 `run.status = running["status"]`。
- Graph 写入 `intake/building_context/deciding/planning/executing_tool/observing/responding/...`。
- partial unique index 只约束 `status='running'`。
- 结果：第一两个 graph node 后 DB 已不再把它视为 active run，同 Session 可并发启动多个 Run，run filters/resume/cancel 也依赖错误状态。

## AR2-P0-002 — `waiting_approval` 不属于活动 Run，审批中可启动第二个 Run

- `_init_run()` busy check 和 DB partial unique 都只看 `running`。
- 审批 interrupt 将 run 改为 `waiting_approval`。
- 新消息随后可创建 Run B 并覆盖 `Session.active_run_id`，Run A 仍可后续 resume。

## AR2-P0-003 — Run lease 只有字段，没有执行所有权

- `Run.lease_owner/lease_expires_at` 存在，但 runner 没有 claim/renew/release/reclaim。
- resume/cancel/tool execution 均不验证 lease。
- worker 在 CAS 或执行中崩溃后会留下不可恢复/可重复执行状态；多 worker 没有真正所有权边界。

## AR2-P0-004 — 副作用 ToolCall 的幂等约束发生在 connector 执行之后

- `(run_id,idempotency_key)` 唯一约束仅在 `_record()` 时触发。
- Broker 在执行前既不查已有 ToolCall，也不持久化 execution claim。
- crash/replay 或两个 worker 可先执行 connector，再在记录阶段发生唯一冲突。
- 对写文件/HTTP mutate 等副作用，这是典型“副作用已发生、幂等锁才生效”的错误顺序。

## AR2-P0-005 — API key hash-at-rest 实际仍保存有效明文

- dev owner 同时写 `api_key=dev_key` 和 `api_key_hash=digest`。
- legacy 回填 hash 不清空 `api_key`。
- auth 永久保留 plaintext fallback。
- HMAC secret 缺失时使用固定默认值。
- DB 泄露仍直接暴露可用 key。

## AR2-P0-006 — Provider API key 同时被写入 JSON，且 CLI `--json` 会原样输出

- `ProviderProfile.to_dict()` 包含 `api_key`。
- `ProviderConfigStore._save()` 因此把 key 明文写进 `profiles.json`。
- `SecretVault` 只是“mirror”，运行时 `get()` 从不读取 vault；`keyring` 甚至不在依赖中。
- `personal-ai config list --json` / `show --json` 直接输出 `to_dict()`，会把完整 key 写到 stdout/日志/CI 管道。

## AR2-P0-007 — Context trust boundary 发生持久化 SYSTEM 权限提升

- `_tier2_profile()` 从用户 memory 读 profile 内容，却把 `profile` 拼进 `system_prompt`。
- `_tier3_task_state()` 可能包含模型/用户派生的 plan objective，同样拼进 system。
- `conversation_summary` 是由历史用户/assistant 文本模型摘要生成，却被作为 `role=system`。
- 这使持久记忆、摘要或任务文本中的恶意指令在后续轮次获得 system 权限，是 persistent prompt-injection privilege escalation。

## AR2-P0-008 — 当前用户消息不在 runtime `state.messages` 中，tool-loop context 被错序，session memory 还会丢用户轮次

- `_init_run()` 只把旧 `recent` 放进 `messages`；当前 `user_input` 仅单独保存在字段和 DB。
- tool observe 后，state 变成 `旧历史 + assistant tool-call + tool result`，当前 user 仍不在其中。
- Context Engine 又把当前 user 最后追加，造成实际顺序可能是 `assistant/tool → current user`，与 OpenAI/DeepSeek tool-call协议和因果顺序相反。
- `_tier3_conversation()` 还移除所有 user 后只把最后一个旧 user 提到前面，进一步改变历史顺序。
- `_compact_session_memory()` 基于 state.messages，导致当前用户轮次在 managed recent/summary 中丢失。

## AR2-P0-009 — `RunStep.data` 仍可能持久化 raw reasoning / reasoning_content

- `_persist_step()` 对 `Run.state` 使用 `_strip_reasoning()`，但 `RunStep.data=dict(update)` 没有同样净化。
- `decide/respond/tool-call` update 可能包含 `thinking` 或 provider `reasoning_content`。
- `/v1/runs/{id}` 又返回 RunStep 数据；因此此前“CoT 不进入公共持久化”的边界并未闭合。

## AR2-P0-010 — Capability deny 只隐藏工具，不阻止 Broker 执行

- `CapabilityService.visible_tools()` 会按 owner 过滤 LLM 可见工具。
- `ToolBroker.execute()` 查到 registry tool 后从不调用 `capabilities.can_use(owner_id, tool_name)`。
- stale tool-call、提示注入或直接构造调用仍可执行被 owner deny 的工具；安全策略只存在 UI/发现层，不在执行边界。

## AR2-P0-011 — Alembic head 与 ORM schema 不一致，第二 migration 还包含大规模错误 UUID 类型变更

- migrations 只有 baseline + `997d...`。
- baseline 没有 `users.api_key_hash`、没有 `event_log`。
- 第二 migration 也没有补这两项，却包含大量 `existing_type=NUMERIC -> UUID` 的自动生成 `alter_column`。
- SQLite 没有 batch migration 配置，这些 ALTER 不可移植。
- `create_all()` 只能创建缺表，不能给现有 users 表补 `api_key_hash`。
- 结论：Alembic head 并不能构造出当前 ORM 所要求的 schema，升级路径本身是不可信的。

## AR2-P0-012 — “durable SSE”通常只持久化 terminal event，快速完成时会丢最终答案

- live `streams.push()` 仅写内存 Queue。
- runner finally 只显式持久化 terminal。
- SSE endpoint 一旦发现任意 durable event 就 replay 后立即 return。
- 因此 run 完成且 queue 已 unregister 时，常见 durable log 只有 `run.completed`；客户端看不到最终 `text.delta`，也不会再进入 state/RunStep fallback。

---

# P1 — 高影响，应在同一修复批次处理

## AR2-P1-001 — Live SSE 是竞争消费而非 broadcast

- 每 run 只有一个 `asyncio.Queue`。
- 多个 subscriber 对同一个 `queue.get()` 竞争，事件会被分片而非每个订阅者收到完整流。

## AR2-P1-002 — EventLog `seq` 永远为 0，没有持久单调序列

- append 固定 `seq=0`；无 `(run_id,seq)` unique。
- replay 再临时编号，无法实现稳定 Last-Event-ID / gap detection / reconnect。

## AR2-P1-003 — Message 去重依赖进程内 `_persisted_message_count`

- worker restart / fresh runner resume 时计数消失。
- Message 没有 run-local durable sequence/unique key。
- replay state 可再次插入已有 assistant/tool messages，污染历史与 compaction。

## AR2-P1-004 — `Session.active_run_id` 只设置不清理

- complete/fail/cancel 没有 conditional clear。
- session 长期指向 terminal run，列表和后续并发逻辑看到陈旧 active owner。

## AR2-P1-005 — Cancel 是 local best-effort，且 finalize 可覆盖 cancelled

- API 先写 cancelled，再吞掉 `runner.cancel()` 任意错误。
- local task cancellation 对其他 worker 无效。
- graph/broker 没有 durable cancellation fence。
- `_finalize()` 无 CAS，可把 cancelled 再写回 completed。

## AR2-P1-006 — Resume 的状态 CAS 与真正恢复不是一个事务语义，失败可留下假 `running`

- API 先 `waiting_approval -> running` commit，再调用 `runner.resume()`。
- `runner.resume()` 的状态校验/approval resolve 部分位于内部 try 之前，可抛错而没有 `_fail()`。
- endpoint 最后还返回 CAS 后缓存的旧 `data`，即使 resume 已完成也可能返回 `running`。

## AR2-P1-007 — Approval resolve 不是原子 pending→resolved

- `resolve()` 先 SELECT `pending`，再在 ORM row 上更新。
- 没有 `SELECT FOR UPDATE` 或 `UPDATE ... WHERE status='pending'` CAS。
- 两个并发 resolver 可都读到 pending 后提交不同结果。

## AR2-P1-008 — R4 `require_auth_method=passkey` 只存在 policy metadata，不被审批/API执行

- Policy R4 返回 `constraints.require_auth_method=passkey`。
- Broker 抛出的 `ApprovalRequiredError` 不传播该约束。
- runner 创建 approval 时因此拿不到 required auth method。
- Approval API 只有 API-key 身份认证，没有 passkey proof，却可能批准未来 R4 工具。

## AR2-P1-009 — Approval engine 缺失时 API 直接改 DB，产生“假批准”

- approval router 在 engine=None 时 fallback 为直接把 row 改 approved/rejected。
- hash binding / expiry / typed concurrency semantic 全部绕过。
- 应 fail closed，而不是把 wiring failure伪装成成功。

## AR2-P1-010 — SSRF DNS rebinding 检查仍是 TOCTOU

- connector 先用 `socket.getaddrinfo()` 检查地址。
- `httpx` connect 时再次独立 DNS resolution。
- attacker 可在 precheck 与 connect 之间改变 DNS；mixed-answer 检测并不能闭合该窗口。
- DNS precheck 失败还被当成“not private”，后续连接会再次解析。

## AR2-P1-011 — Filesystem symlink race 与 regex ReDoS 仍未真正关闭

- read/write 在 `realpath` 检查后再普通 `open()`，write overwrite 路径存在 symlink swap 窗口。
- read 打开后用 `realpath(fh.name)` 仍是路径解析，不是已打开 fd 的真实目标验证。
- regex search 只放进 thread；Python catastrophic regex 可长期占 CPU，thread timeout不能终止正在运行的正则。

## AR2-P1-012 — Production checkpointer 是 node-local SQLite，Compose 中甚至没有持久化到挂载 volume

- `_open_persistent_checkpointer()` 始终使用 `~/.personal_ai/checkpoints.sqlite`。
- production DB 即使是 Postgres，checkpoint 仍不共享 worker。
- compose 只挂载 `./data:/app/data`，却没有设置 `PERSONAL_AI_DATA_DIR=/app/data`；默认 checkpoint 实际写 `/root/.personal_ai`，容器重建会丢失审批 interrupt state。

## AR2-P1-013 — Compose 没有设置 `APP_ENV=production`

- API production guard、迁移纪律等由 APP_ENV 决定。
- 官方 personal-server compose 没有该变量，因此默认运行 `development` 语义并执行 `create_all()`。

## AR2-P1-014 — Production startup 仍 `Base.metadata.create_all()`，没有校验 Alembic head

- `ensure_database()` 无论环境都调用 `init_db()`。
- production 部署会静默创建缺表，但不会做 schema alter，也不会检测 DB revision 落后。
- 可形成“应用启动成功、首个查询才因缺列失败”的半升级状态。

## AR2-P1-015 — `/readyz` not-ready 仍返回 HTTP 200

- 返回 JSON `{status:not_ready}`，但没有非 2xx status。
- orchestrator/负载均衡按 HTTP status 的 readiness probe 会继续向不可服务实例分流。

## AR2-P1-016 — Request body limit 只信任 Content-Length，可用 chunked/无 Content-Length 绕过

- middleware 只有 header 检查，没有包装 ASGI receive 统计实际字节。
- 攻击者可以发送超限 streaming body，让框架继续读入。

## AR2-P1-017 — SessionRouter find-or-create 存在 SELECT→INSERT race，DB 也没有对应 unique index

- `(owner, channel, external_conversation_id, active)` 没有数据库唯一约束。
- 两个首消息并发都可能查询不到后各自创建 active session。

## AR2-P1-018 — CredentialBroker 注入的 `_secrets` 在 connector 前被无条件剥离

- broker 调 `credential_broker.inject()`，得到 `_secrets`。
- connector execution 前直接删除 `_secrets`，且 execution context 没有独立 secret channel。
- 所有声明 `credential_scope` 的 connector 实际拿不到凭据；安全抽象与功能契约不一致。

## AR2-P1-019 — High-risk durable intent 会原样保存 arguments，未走统一 sanitizer

- R3/R4 `_durable_intent()` 将 `arguments` 直接放 `AuditEvent.details`。
- 如果 headers/body/arguments 中含 token/password，自身审计日志成为秘密副本。

## AR2-P1-020 — Tool registry 重名允许覆盖 descriptor，但 connector routing 用 `setdefault`

- registry 对同名工具只 warning 后覆盖。
- broker connector mapping 保留第一次注册的 connector。
- 插件/未来 connector 名称碰撞时可形成“descriptor B + executor A”的 capability confusion。

## AR2-P1-021 — CLI/SSE 没有真正可恢复 reconnect protocol

- server 没有 SSE `id:` / Last-Event-ID 语义，event_id 只放 JSON envelope 且每 replay 重生成。
- client 只检测单连接 seq gap，不自动从 durable seq 恢复。
- 这与当前“durable stream”设计目标不一致。

---

# P2 — 次级问题 / 清理项

## AR2-P2-001 — Unknown model price 仍被 provider 写成 `$0.00`

- runner 支持 `cost_usd=None` 表示价格未知；但 OpenAI-compatible provider 默认产生 `0.0`，语义仍是假零成本。

## AR2-P2-002 — `ALL_MODELS` 定义早于 `EventLog`，集合遗漏 EventLog

- 使用该集合的未来 migration/schema tooling 会漏掉 event log。

## AR2-P2-003 — MemoryStore `get/update/forget` 自身没有 owner 参数

- 当前 REST route 先做 owner 查询，因此公共 API 未直接越权。
- 但 store 作为跨模块 service 时，仅凭 memory_id 即可修改其他 owner memory；属于 latent ownership footgun。

## AR2-P2-004 — 多处 broad `except Exception` 把 wiring/audit/event failures 降级为 warning

- 对普通 telemetry 可接受；但 security-sensitive service（approval/capability/checkpointer）不应和 optional UI 一样 fail-soft。

---

# 旧审查结论的重新判定

本轮发现多项旧 issue 虽有“fix commit”，但只是部分落地：

- session concurrency：只有 `status='running'` index，且 status 被 graph phase 覆盖 → **未修复**。
- run lease：只有 schema 字段 → **未修复**。
- ToolCall idempotency：只有 post-execution unique constraint → **未修复核心语义**。
- API key hashing：hash 与 plaintext 并存 → **未达到 at-rest 目标**。
- durable SSE：仅 terminal durable → **未达到 replay 目标**。
- provider keyring：仅 mirror 且没有读取，JSON/JSON CLI仍泄露 → **未修复**。
- SSRF rebinding：只做双 DNS precheck → **未关闭 TOCTOU**。

---

# 修复优先级冻结

下一阶段按依赖顺序处理：

1. **Schema/migration + lifecycle invariant**（P0-001/002/003/011，P1-003/004/005/006/012/013/014/017）
2. **Tool execution security**（P0-004/010，P1-007/008/009/018/019/020）
3. **Context/message correctness**（P0-007/008/009，P1-003）
4. **Secrets/auth**（P0-005/006）
5. **SSE durability/broadcast**（P0-012，P1-001/002/021）
6. **Connector hardening**（P1-010/011）
7. **API operational guards**（P1-015/016）
8. **P2 cleanup + regression gates**

任何修复都必须带对应回归测试；数据库相关改动必须通过 Alembic；最终以 Python 3.12/3.13 CI + migration smoke + targeted concurrency/security tests 作为验收。