# 14 — Observability / Doctor / Diagnostics

## 1. `personal-ai doctor`

检查顺序：
1. CLI version / Python
2. config parse
3. API URL
4. API healthz
5. auth
6. active provider profile
7. provider config sanity（不一定真实消耗 token）
8. terminal capabilities
9. writable local config/cache dir
10. server-reported tools
11. optional DB/server diagnostics（仅 API 暴露时）

输出：
```text
OK   API          http://localhost:8000 · 18ms
OK   Auth         configured
OK   Provider     glm-main
WARN Terminal     truecolor unavailable; using 256-color
OK   Tools        3 registered
```

---

## 2. `--debug`
- 显示 stack trace 到 stderr；
- HTTP request id；
- event names；
- timings；
- secret redaction 后 payload summary。

默认不打印 stack。

---

## 3. Diagnostic bundle（P2）
可导出：
- CLI version
- OS/terminal
- redacted config
- recent client logs
- session/run ids
- event schema
- server health

不包含：
- API key
- credentials
- full memory content
- raw chain-of-thought
- arbitrary file content

---

## 4. TUI diagnostics
`/status` 面向用户；
`/debug` 面向开发者。

状态栏 connection：
- connected
- reconnecting
- offline
- auth failed

---

## 5. 性能指标
开发测试可记录：
- startup first paint
- API handshake
- prompt -> run.started
- token -> paint latency
- event queue depth
- render FPS
- max transcript cells
- memory usage

目标不是追求 60fps，而是输入和事件不阻塞。
