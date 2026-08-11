# 20 — Current → Target 文件改造地图

> 此文件用于让执行 Agent 从现有 `apps/cli/main.py` 的函数，准确路由到 v2 模块。  
> 实施时必须先重新读取仓库最新版本，若路径有变化，以职责映射为准。

## 1. `apps/cli/main.py` 拆分

| 当前职责/函数 | 目标位置 | Phase | 处理 |
|---|---|---:|---|
| `APIClient` | `cli/api/client.py` | 1 | 改 async typed client |
| API error print/SystemExit | `cli/api/errors.py` + renderer | 1 | 删除 transport 输出 |
| `_ensure_session` | session service/controller | 1/3 | 返回 DTO，不 print |
| `_print_reply` | headless text renderer | 1 | 仅 headless |
| `_stream_run` | `api/sse.py` + controller | 1 | decoder 与 render 分离 |
| `_apply` | `domain/normalizer.py` + reducer | 1 | 不直接 stdout |
| ANSI constants | `tui/theme.py` | 2 | 语义 token |
| `cmd_chat` | TUI bootstrap | 2 | 替换 input loop |
| `cmd_send` | `commands/exec.py` | 1 | 新 headless 契约 |
| `cmd_sessions` | `commands/sessions.py` | 1/3 | table/json |
| `cmd_runs` | `commands/runs.py` | 1/4 | P4 用正式 list API |
| `cmd_memories` | `commands/memories.py` | 3 | 完整 CRUD projection |
| `cmd_approve` | `commands/approvals.py` + TUI modal | 3 | approve/reject/edit |
| config wizard | `commands/config.py` / config store | 1/5 | 保留数据格式兼容 |
| `build_parser` | `main.py` Typer tree | 1 | root 只 dispatch |
| `main()` | entry point | 1 | bootstrap |

---

## 2. 建议最终源码位置

若采用可安装 package 的最佳结构：

```text
src/personal_ai_os/cli/
├── main.py
├── bootstrap.py
├── api/
│   ├── client.py
│   ├── dto.py
│   ├── errors.py
│   └── sse.py
├── domain/
│   ├── events.py
│   ├── normalizer.py
│   ├── state.py
│   ├── reducer.py
│   └── models.py
├── controllers/
│   ├── chat.py
│   ├── sessions.py
│   └── approvals.py
├── commands/
│   ├── exec.py
│   ├── sessions.py
│   ├── runs.py
│   ├── approvals.py
│   ├── memories.py
│   ├── tools.py
│   ├── audit.py
│   ├── config.py
│   └── doctor.py
├── output/
│   ├── text.py
│   ├── json_output.py
│   └── jsonl.py
└── tui/
    ├── app.py
    ├── command_registry.py
    ├── keymap.py
    ├── theme.py
    ├── capabilities.py
    ├── widgets/
    └── screens/
```

### 为什么推荐从 `apps/cli` 迁到 `src/personal_ai_os/cli`
当前 `pyproject.toml` wheel target 明确包含 `src/personal_ai_os`。把正式 CLI package 放进 `src`：
- 安装语义更自然；
- entry point 稳定；
- `apps/` 可以继续作为 server/application composition；
- 测试不依赖 repo cwd。

迁移期间 `apps/cli/main.py` 可保留 compatibility shim：
```python
from personal_ai_os.cli.main import main

if __name__ == "__main__":
    main()
```

---

## 3. API 侧 P1 文件

### `apps/api/routers/runs.py`
新增：
```text
GET /v1/runs
  session_id?
  status?
  limit
  cursor?
```
必须 owner-scoped、pagination、tests。

### `apps/api/routers/stream.py`
目标不是继续堆 if，而是：
- versioned event envelope；
- live/replay 使用同一 serializer；
- sequence/event id；
- stable tool_call id；
- compatibility path。

建议把 event serializer 下沉：
```text
src/personal_ai_os/common/events.py
或
apps/api/event_serialization.py
```
具体位置依据现有 event domain 决定。

### `apps/api/routers/approvals.py`
P0 不必改审批语义。
P1 只在需要 enriched DTO 时改 serializer/schema。

### session router/serializer
Session selector 若发生 N+1，再补 list summary fields，不要客户端并发 GET 每个 session 作为长期方案。

---

## 4. 测试文件地图

```text
tests/unit/cli/
├── test_api_client.py
├── test_sse_decoder.py
├── test_event_normalizer.py
├── test_reducer.py
├── test_headless_text.py
├── test_headless_json.py
├── test_jsonl.py
├── test_command_registry.py
├── test_terminal_sanitizer.py
└── test_error_mapping.py

tests/integration/cli/
├── test_chat_complete.py
├── test_tool_lifecycle.py
├── test_approval_resume.py
├── test_approval_reject.py
├── test_approval_edit.py
├── test_cancel.py
├── test_stream_recovery.py
└── test_legacy_aliases.py
```

Textual/renderer snapshot：
```text
tests/snapshots/cli/
```

---

## 5. 最小安全拆分顺序

不要第一步移动所有代码。

### Commit A
抽 `APIClient` + errors，行为不变。

### Commit B
抽 SSE decoder，旧 print renderer 仍消费 decoder。

### Commit C
加入 UIEvent + reducer，旧 renderer 改消费 UIEvent。

### Commit D
建立 headless renderer + `exec`。

### Commit E
切 Typer command tree，保留 aliases。

### Commit F
加 TUI shell，复用同一 core。

这样任一步都可回滚，且容易定位 regression。

---

## 6. P0 明确“不改”

除非测试暴露阻塞：
- LangGraph graph topology；
- ToolBroker policy decision；
- memory retrieval algorithm；
- DB schema；
- provider routing；
- approval hash semantics。

CLI v2 的第一轮成功标准是“把已有能力正确、稳定、可理解地呈现出来”。
