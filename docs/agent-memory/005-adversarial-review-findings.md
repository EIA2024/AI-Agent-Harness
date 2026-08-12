# 005 — 全分支对抗性审查：问题登记（先记录，后修复）

> Branch: `cli-v2-upgrade`
> Initial reviewed HEAD: `fd24100ece9936a32e4c887126e4bb8279758b2e`
> Review mode: adversarial / fail-closed / concurrency + durability + security oriented
> Status: **REVIEW IN PROGRESS**

本文件遵循本轮要求：**先登记问题与证据，不在完整审查结束前给出或实施修复方案。**
后续会在全链路审查完成后补齐问题清单，再另建 remediation plan，之后才批量修复与验证。

## 严重度

- `P0`: 可直接破坏安全边界、产生错误外部副作用、严重数据/执行一致性问题，或让核心业务语义失效。
- `P1`: 高概率造成错误行为、数据重复/丢失、并发/恢复失败、明显用户可见故障。
- `P2`: 中低影响、可维护性或未来扩展风险。

---

## 第一批高影响 VERIFIED findings

### AR2-P0-001 — Run.status 被 Graph phase 覆盖，导致“每 Session 仅一个活动 Run”约束几乎立即失效

- **Severity**: P0
- **Status**: OPEN / VERIFIED
- **Files**: `src/personal_ai_os/agent_runtime/runner.py`, `src/personal_ai_os/agent_runtime/graph.py`, `src/personal_ai_os/db/models.py`
- **Evidence**:
  - Run 创建时 `status="running"`。
  - DB partial unique index 只约束 `status = 'running'`。
  - `_persist_step()` 每个 graph node 都执行 `run.status = status or running.get("status") or run.status`。
  - Graph 节点持续把 state.status 改为 `intake`, `building_context`, `deciding`, `planning`, `executing_tool`, `observing`, `responding`, `reflecting`, `committing_memory`。
- **Impact**: 第一两个 graph node 落库后 Run.status 就不再是 `running`，唯一索引失去保护；同一 Session 可并发启动多个 Run。`list/filter/resume/cancel` 也会看到并依赖错误的生命周期状态。

### AR2-P0-002 — `waiting_approval` 不属于 Session 活动 Run 约束，审批暂停期间可启动第二个 Run

- **Severity**: P0
- **Status**: OPEN / VERIFIED
- **Files**: `src/personal_ai_os/db/models.py`, `src/personal_ai_os/agent_runtime/runner.py`, `apps/api/routers/messages.py`
- **Evidence**:
  - partial unique index 仅匹配 `status='running'`。
  - `_init_run()` busy 检查也仅查询 `Run.status == "running"`。
  - approval interrupt 会把 run.status 改成 `waiting_approval`。
  - `post_message()` 随后允许创建新 Run，并覆盖 `Session.active_run_id`。
- **Impact**: 审批中的 Run A 与新 Run B 可同时属于同一会话；之后恢复 A 时可与 B 冲突、上下文/active_run 指针分叉，并破坏“串行会话”安全假设。

### AR2-P0-003 — Run lease 只存在于 schema，执行路径从不 claim/renew/release/reclaim

- **Severity**: P0
- **Status**: OPEN / VERIFIED
- **Files**: `src/personal_ai_os/db/models.py`, `src/personal_ai_os/agent_runtime/runner.py`, `apps/api/routers/runs.py`
- **Evidence**:
  - `Run` 有 `lease_owner` / `lease_expires_at` 字段。
  - 当前 `RunRunner` 没有任何 lease claim、续租、校验、释放或过期回收逻辑。
  - `/resume` 只做 `waiting_approval -> running` CAS 后直接调用 `runner.resume()`，没有 worker lease。
- **Impact**: worker 在 CAS 后或执行中崩溃，Run 仍可永久卡死；多个 worker 也没有真正的执行所有权边界。之前宣称完成的 P1-027 实际只完成了 schema。

### AR2-P0-004 — API key“hash 修复”仍把有效明文 key 存进数据库

- **Severity**: P0
- **Status**: OPEN / VERIFIED
- **Files**: `apps/api/deps.py`, `src/personal_ai_os/db/models.py`
- **Evidence**:
  - 新 dev owner 同时写 `api_key=dev_key` 与 `api_key_hash=digest`。
  - legacy row 回填 hash 后不清空 `api_key`。
  - `resolve_user()` 永久保留 `(hash == digest) OR (api_key == raw_key)` 明文回退。
  - HMAC secret 在环境变量缺失时使用固定默认值 `personal-ai-os-key-hash`。
- **Impact**: DB 泄露仍直接暴露可用 API key；所谓 hash-at-rest 安全目标未实现。固定 HMAC secret 还削弱了弱 key 情况下的离线保护。

### AR2-P0-005 — “durable SSE event log”通常只持久化 terminal event，晚订阅者会丢失整个答案/工具过程

- **Severity**: P0
- **Status**: OPEN / VERIFIED
- **Files**: `src/personal_ai_os/agent_runtime/event_log.py`, `src/personal_ai_os/agent_runtime/streams.py`, `src/personal_ai_os/agent_runtime/runner.py`, `apps/api/routers/stream.py`
- **Evidence**:
  - `streams.push()` 只向进程内 Queue 写事件，不写 EventLog。
  - `_persist_live()` 虽存在但没有接到 push 路径。
  - `_run_background()` 只显式 `append_run_event()` terminal event。
  - SSE endpoint 只要 durable log 非空就直接 replay durable 后 `return`，不再走 RunStep/state fallback。
- **Impact**: 快速完成的 Run 在客户端建立 SSE 连接前已 unregister 时，durable log 往往只有 `run.completed`；客户端因此看不到最终文本和工具事件。P1-020 的“跨进程 durable stream”声明与实际实现不符。

### AR2-P1-001 — Live SSE 使用单个共享 Queue，不是 broadcast；多个订阅者会互相抢事件

- **Severity**: P1
- **Status**: OPEN / VERIFIED
- **Files**: `src/personal_ai_os/agent_runtime/streams.py`, `apps/api/routers/stream.py`
- **Evidence**: `streams.get(run_id)` 返回唯一 `asyncio.Queue`；每个 SSE subscriber 都调用同一个 `queue.get()`。
- **Impact**: 两个客户端同时观察同一 Run 时事件被竞争消费，每个客户端只能收到子集，流不可重放/不可观察。

### AR2-P1-002 — Message 去重仍完全依赖进程内计数，进程重启/恢复后会重复落库

- **Severity**: P1
- **Status**: OPEN / VERIFIED
- **Files**: `src/personal_ai_os/agent_runtime/runner.py`, `src/personal_ai_os/db/models.py`
- **Evidence**:
  - `_persisted_message_count` 是进程内 dict。
  - `Message` 没有 run-local sequence/event id 唯一约束。
  - resume/restart 路径没有从数据库恢复已持久化 message index。
  - 此前 P1-011 的 DB 唯一约束只加在 `ToolCall(run_id,idempotency_key)`。
- **Impact**: checkpoint 恢复或 worker 重启后，可把 state 中已有 user/assistant/tool message 再写一次，污染历史、记忆压缩和后续上下文。

### AR2-P1-003 — `Session.active_run_id` 只写不清，终态后仍长期指向已完成/失败/取消 Run

- **Severity**: P1
- **Status**: OPEN / VERIFIED
- **Files**: `apps/api/routers/messages.py`, `src/personal_ai_os/agent_runtime/runner.py`
- **Evidence**: message endpoint 在新 Run 后设置 `active_run_id`; `_finalize()`, `_fail()`, `cancel()` 未条件清理该指针。
- **Impact**: Session 列表的 `active_run_status` 可长期显示 terminal run；并发/恢复逻辑如果使用该字段会得到陈旧所有权信息。

### AR2-P1-004 — Cancel 是 fail-soft，跨 worker 时执行可能继续，后续 finalize 还能覆盖 cancelled 状态

- **Severity**: P1
- **Status**: OPEN / VERIFIED
- **Files**: `apps/api/routers/runs.py`, `src/personal_ai_os/agent_runtime/runner.py`
- **Evidence**:
  - API 先把 DB status 写为 `cancelled`，再 best-effort 调 `runner.cancel()`，任意异常被吞掉。
  - 取消只会 cancel 当前进程 `_bg_tasks` 中的 task。
  - graph worker 没有每 step 检查 durable cancel 状态。
  - `_finalize()` 无 CAS，仍可把 Run.status 直接改成 `completed`。
- **Impact**: 在 multi-worker/进程重启场景中，用户看到“已取消”，实际 connector/model 仍可能继续；执行完成后状态还可能反转为 completed。

### AR2-P1-005 — EventLog.seq 永远为 0，缺少 run 内唯一/单调序列

- **Severity**: P1
- **Status**: OPEN / VERIFIED
- **Files**: `src/personal_ai_os/agent_runtime/event_log.py`, `src/personal_ai_os/db/models.py`
- **Evidence**: `append_run_event()` 固定 `seq=0`；EventLog 无 `(run_id, seq)` 唯一约束；SSE replay 又临时重新编号。
- **Impact**: 无法可靠支持 Last-Event-ID、gap detection、跨 worker 有序 replay；所谓 durable sequence 不是持久语义。

---

## 观察到但需继续验证的迁移风险

### AR2-INV-001 — 第二个 Alembic migration 含大量 NUMERIC→UUID alter_column

- **Status**: INVESTIGATING
- **Files**: `migrations/versions/997d7d9eed7e_run_lease_session_concurrency_tool_.py`, `migrations/env.py`
- **Evidence**: migration 除新增 lease/index 外，对几乎所有 UUID 字段生成 `existing_type=NUMERIC -> UUID` 的 alter；env 未启用 SQLite batch mode。
- **Risk hypothesis**: 可能是 SQLite reflection/autogenerate 噪声，在 PostgreSQL/SQLite upgrade path 上造成不可移植或破坏性迁移。
- **Rule**: 完整检查 migration 与 CI upgrade path 后再决定是否升级为 VERIFIED finding。

---

## 接下来继续审查的区域

- API ownership / auth / approval / cancellation / schema limits
- Graph / runner lifecycle / checkpoint / lease / idempotency
- ToolBroker / policy / credentials / security audit
- HTTP / filesystem connector sandbox + SSRF + side-effect metadata
- Context / memory trust + prompt injection
- Model gateway retries / tool alias / secret handling
- SSE / CLI event semantics / reconnect / multi-subscriber
- DB constraints / Alembic upgrade & downgrade / SQLite + PostgreSQL portability
- deployment defaults / startup readiness
- test gaps / CI gates / failure injection

完整审查结束前，本文件保持 findings-only；修复方案另文编写。