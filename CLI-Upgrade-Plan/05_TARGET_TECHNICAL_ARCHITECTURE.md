# 05 — CLI v2 技术架构

## 1. 推荐 Python 结构

```text
apps/cli/
├── __init__.py
├── main.py                 # Typer root，只负责参数和 dispatch
├── bootstrap.py            # config + client + TUI wiring
├── api/
│   ├── client.py           # AsyncAPIClient
│   ├── dto.py
│   ├── errors.py
│   └── sse.py
├── domain/
│   ├── events.py           # UIEvent
│   ├── state.py            # AppState
│   ├── reducer.py
│   ├── selectors.py
│   └── models.py
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
├── tui/
│   ├── app.py
│   ├── keymap.py
│   ├── command_registry.py
│   ├── theme.py
│   ├── capabilities.py
│   ├── widgets/
│   │   ├── transcript.py
│   │   ├── composer.py
│   │   ├── status_bar.py
│   │   └── cells/...
│   └── screens/
│       ├── session_picker.py
│       ├── approval.py
│       ├── model_picker.py
│       ├── help.py
│       └── details.py
├── render/
│   ├── markdown.py
│   ├── tool.py
│   ├── approval.py
│   └── diff.py
├── output/
│   ├── text.py
│   ├── json.py
│   └── jsonl.py
└── config_store.py
```

如项目希望把可发布代码都放进 `src/personal_ai_os`，可改成
`src/personal_ai_os/cli/*`。关键是**不要让 entry point 指向一个 wheel 不包含的 `apps` module**。

---

## 2. 推荐依赖

首选：
- **Typer**：命令树、help、类型参数；
- **Textual**：async TUI/reactive widget/screens；
- **Rich**：Markdown/table/syntax；
- **httpx**：保留；
- SSE：优先成熟 decoder 库；若自研必须完整 fixture tests。

实施前通过 `ADR-001` 做 1 天以内的技术 spike：
- first paint；
- SSE 1000 event；
- resize；
- Windows Terminal；
- inline/alternate screen；
- snapshot；
- `NO_COLOR`；
- copy/selection。

若 Textual 无法满足 inline/scrollback 目标，可退到 `prompt_toolkit + Rich`，但架构层保持不变。

---

## 3. AsyncAPIClient

禁止：
```python
def get(...):
    ...
    print(error)
    raise SystemExit
```

目标：
```python
class APIError(Exception):
    status_code: int | None
    detail: str
    retryable: bool

class AsyncAPIClient:
    async def create_session(...) -> SessionDTO: ...
    async def send_message(...) -> RunStartDTO: ...
    async def get_run(...) -> RunDTO: ...
    async def cancel_run(...) -> RunDTO: ...
    async def resolve_approval(...) -> ApprovalDTO: ...
    async def stream_run(...) -> AsyncIterator[ServerEvent]: ...
```

所有 auth/url/timeout 放在 client config。

---

## 4. Event normalization

Server events 不直接进入 UI：

```text
ServerEvent(type="thinking.delta", data=...)
  -> normalize
UIEvent(type="progress", ...)
```

示例：

```python
@dataclass(frozen=True)
class UIEvent:
    schema_version: int
    type: UIEventType
    run_id: UUID
    ts: datetime | None
    seq: int | None
    payload: Mapping[str, Any]
```

Normalizer 负责：
- live/replay 差异；
- legacy event name；
- payload fallback；
- redaction；
- raw thinking suppression；
- unknown event -> diagnostic notice，而不是 crash。

---

## 5. Reducer

```python
def reduce(state: AppState, event: UIEvent) -> AppState:
    ...
```

规则：
- pure-ish；
- no network；
- no filesystem；
- deterministic；
- tests 用 event fixture 重放即可还原 UI state。

关键状态：
- current session；
- current run；
- transcript cells；
- tool call map；
- pending approval；
- connection state；
- current model/profile；
- overlay；
- queued user input。

---

## 6. Side-effect controller

网络动作由 controller/service 发起：
- send prompt；
- approve；
- reject；
- edit；
- cancel；
- resume；
- load history。

TUI action -> controller -> API -> event -> reducer。

不要 Widget 里：
`await httpx.post(...)`。

---

## 7. TUI 与 headless 共用层

共用：
- API client
- DTO
- SSE decoder
- event normalizer
- command/config
- redaction
- error taxonomy

不共用：
- Textual Widget 与 stdout printer。

这保证 CI 不需要启动 Textual。

---

## 8. Entry point

建议 `pyproject.toml`：

```toml
[project.scripts]
personal-ai = "personal_ai_os.cli.main:app"
```

如果 CLI 继续在 `apps/`：
必须同时调整 hatch wheel package inclusion，且安装 wheel 后做 smoke test。

---

## 9. 配置分层

```text
defaults
< user config
< project config
< environment
< CLI flags
```

敏感 API key 不进入 project config。

每个 config value 能显示来源：
`personal-ai config show --sources`

---

## 10. 错误分类

- `AuthError`
- `NotFoundError`
- `ConflictError`
- `RateLimitError`
- `ServerError`
- `TransportError`
- `StreamProtocolError`
- `ConfigError`

UI 根据错误类型给恢复动作，而不是字符串匹配。
