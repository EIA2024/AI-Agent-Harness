# 01 — 当前仓库与 CLI 审计

## 1. 当前系统结构

现有仓库不是一个简单 chat bot，而是长期运行的 Personal AI OS：

```text
apps/cli ─┐
apps/api ─┼─> Gateway / Session Router
          │      -> Agent Runtime (LangGraph)
          │      -> Context Engine
          │      -> Model Gateway
          │      -> Tool Broker -> Policy/Approval/Credential -> Connectors
          │      -> Memory Engine
          │      -> Audit / DB
```

`src/personal_ai_os/` 已按领域拆为：
- `agent_runtime`
- `common`
- `context_engine`
- `db`
- `gateway`
- `memory_engine`
- `model_gateway`
- `observability`
- `policy_engine`
- `scheduler`
- `tool_broker`

因此 CLI 升级应主要做“能力投影”，不是再造 Runtime。

---

## 2. 当前 CLI 的真实状态

`apps/cli/main.py` 同时承担：

- CLI parser
- sync HTTP client
- exit/error handling
- Session 创建
- SSE parsing
- ANSI style
- streaming render
- interactive loop
- approvals
- memories
- runs
- provider config wizard

这导致 5 类耦合：

### C1. Transport 与 Presentation 耦合
网络异常时 API Client 直接 `print` + `SystemExit`，无法在 TUI 内以恢复态表示。

### C2. SSE 与 stdout 耦合
`_stream_run()` 在解析 SSE 时直接渲染，无法：
- replay fixture；
- snapshot；
- JSONL；
- 多 renderer；
- reconnect。

### C3. 事件信息丢失
当前 `_apply()`：
- `tool.requested` 只打印工具名；
- `tool.completed / tool.failed` 直接 silent；
- approval 只换行；
- run status 信息很少。

### C4. API 已有能力未暴露
后端已有：
- approval reject；
- approval edit arguments；
- run cancel；
- run resume；
- run detail tool_calls/steps；
- audit；
- tools；
- automation CRUD。

CLI 没有完整映射。

### C5. runs list 不是 runs list
当前逻辑通过 session `active_run_id` 反推，而服务端没有正式 `/v1/runs` list。
历史 run 可见性与查询能力不足。

---

## 3. SSE 当前契约问题

服务端 `/v1/runs/{id}/stream`：
- live 时从 runtime queue 推事件；
- run 结束后从 DB replay；
- `approval.required` 会终止当前 SSE；
- resume 后客户端需要重新订阅；
- replay 通过 RunStep 映射 event，与 live payload 结构并非天然一致。

当前客户端手写 parser 只处理单行 `data:`，没有系统处理：
- event id；
- retry；
- multiline data；
- heartbeats/comments；
- unexpected EOF；
- duplicate/reconnect；
- sequence / dedupe。

目标：先在 client 建 normalization，后在 P1 把 server envelope 统一。

---

## 4. Approval 当前能力

API 已支持：
- `GET /v1/approvals?status=pending`
- `POST .../approve`
- `POST .../reject`
- `POST .../edit`
- `POST /v1/runs/{run_id}/resume`

且 Runtime/Policy 具有：
- exact argument hash binding；
- R0–R4 risk model；
- broker re-verification。

CLI 必须利用这些能力，而不是自行实现权限。

---

## 5. Packaging 缺口

当前 `pyproject.toml`：
- Python >= 3.12；
- 依赖 FastAPI/httpx/LangGraph 等；
- dev 有 pytest/ruff；
- 未看到明确的 `[project.scripts] personal-ai = ...`；
- 未声明专门的 TUI/CLI UI 库。

Phase 0 应明确：
- 安装入口；
- CLI dependency；
- `python -m` fallback；
- wheel 中 `apps/cli` 的打包策略。

> 注意：当前 wheel target 只明确 `src/personal_ai_os`。如果 CLI 位于 `apps/`，需验证安装后的 entry point 能否 import。不要只在源码 checkout 下测试。

---

## 6. 现有测试面

仓库已有大量 API/Runtime/Policy/Tool 单元测试，但 CLI 没有形成同等级的 contract/snapshot 测试层。

CLI v2 必须新增：
- `tests/unit/cli/`
- `tests/integration/cli/`
- SSE fixture
- UI event reducer tests
- renderer snapshot/golden tests
- headless stdout/stderr tests
- approval flow tests

---

## 7. 优先修复矩阵

| 问题 | 影响 | 难度 | Phase |
|---|---:|---:|---|
| main.py 单体 | 高 | 中 | 1 |
| API client 直接 print/exit | 高 | 低 | 1 |
| SSE parser 不可靠 | 高 | 中 | 1 |
| stdout/stderr 无契约 | 高 | 低 | 1 |
| approval UI 不完整 | 高 | 中 | 3 |
| run cancel/resume 不直观 | 高 | 中 | 3 |
| session 恢复弱 | 高 | 中 | 3 |
| tool 生命周期不可见 | 中 | 中 | 2/3 |
| raw thinking 默认展示 | 中/安全 | 低 | 1 |
| run list API 缺失 | 中 | 中 | 4 |
| audit/tools/config 不统一 | 中 | 低 | 5 |
| responsive/accessibility | 中 | 中 | 2/5 |
