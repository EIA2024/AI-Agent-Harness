# 15 — 测试策略与质量门禁

## 1. 测试金字塔

### Unit
- SSE decoder
- event normalizer
- reducer
- command registry
- output renderer
- error mapping
- redactor
- width/layout helper

### Component snapshot
- transcript cell
- tool states
- approval modal
- session picker
- errors
- 60/80/120/160 width

### Integration
mock FastAPI/server：
- new session -> send -> stream -> complete
- tool requested -> completed
- waiting approval -> approve -> resume -> complete
- reject
- edit args
- cancel
- live stream disconnect -> recovery
- replay completed run

### E2E
真实 local API + Echo/Fake provider：
- startup
- message
- tool
- approval
- exit/resume

---

## 2. 必备 SSE fixtures

```text
simple_complete.sse
thinking_and_text.sse
tool_complete.sse
tool_failed.sse
approval_pause.sse
multiline_data.sse
comments_heartbeat.sse
unexpected_eof.sse
unknown_event.sse
replay_run.sse
duplicate_event.sse
```

---

## 3. stdout contract tests

pytest 捕获：
- stdout
- stderr
- exit code

断言：
- `exec -o stream-json` stdout 每行 json.loads 成功；
- stderr 无 secret；
- `text` stdout 没 ANSI；
- failure exit code 正确；
- approval required 不自动批准。

---

## 4. Security tests
- ANSI injection
- OSC injection
- malicious URL
- very long unbroken line
- bidi/control chars
- secret redaction
- approval args
- unknown server fields

---

## 5. Cross-platform
CI matrix至少：
- ubuntu latest
- windows latest
- macOS latest（资源允许）
- Python 3.12 + 当前支持版本

Windows 专测：
- console unicode width
- Ctrl+C
- path
- terminal resize

---

## 6. Performance gate
建议：
- 10000 assistant delta event replay 不丢顺序；
- 1000 tool events reducer 不崩；
- 10 MB tool output 不进入全部 widget DOM；
- terminal resize 100 次不异常；
- first paint 不被 provider network check 阻塞。

---

## 7. Gate
每 Phase 必须：
```bash
ruff check .
pytest -q
```

如引入 type checker，再加：
`pyright/mypy`，但不要一次性让整个旧仓库 type debt 阻断 CLI 改造。
