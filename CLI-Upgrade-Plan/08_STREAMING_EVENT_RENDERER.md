# 08 — Streaming / Event / Renderer 方案

## 1. 当前风险

当前客户端手写 SSE 并直接 print，且服务端 live/replay payload 并不完全对称。

这会产生：
- replay 重复 final；
- tool lifecycle 丢失；
- reconnect 后重复事件；
- multiline `data:` 解析错误；
- stream EOF 无法区分正常与异常；
- approval resume 后难以延续同一 transcript。

---

## 2. P0：客户端 normalization

### ServerEvent
保留原始：
```text
event
id?
data
retry?
```

### Normalized UIEvent
建议：
```json
{
  "schema_version": 1,
  "type": "tool.completed",
  "run_id": "...",
  "seq": null,
  "timestamp": null,
  "payload": {}
}
```

支持类型：
- connection.opened/closed
- run.started
- progress
- assistant.delta
- assistant.completed
- tool.requested
- tool.started
- tool.completed
- tool.failed
- approval.required
- run.completed
- run.failed
- run.cancelled
- warning
- error
- unknown

---

## 3. Raw thinking 处理

`thinking.delta`：
- 默认 normalize 为 `progress` 或 drop；
- 不默认写 stdout；
- replay 不应把完整 thinking 当一段最终用户内容；
- 后端未来加入 `reasoning.summary` 时优先使用。

---

## 4. Tool lifecycle

UI reducer 维护：
```python
tool_calls: dict[tool_call_id, ToolViewState]
```

状态：
`requested -> running -> completed|failed`

若 legacy event 缺 id：
- 生成仅限 client session 的 stable fallback id；
- 输出 diagnostic warning；
- P1 修服务端。

---

## 5. Approval resume

流程：

```text
SSE -> approval.required -> stream closes
                |
                v
        fetch approval detail
                |
        user approve/reject/edit
                |
                v
POST /runs/{id}/resume
                |
                v
re-subscribe /stream
```

Reducer 必须保留原 transcript，不新建假的 Run。

---

## 6. P1：服务端 Event Envelope

建议服务端所有 live/replay 均发：

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "seq": 17,
  "timestamp": "2026-...",
  "run_id": "...",
  "type": "tool.completed",
  "data": {
    "tool_call_id": "...",
    "tool_name": "...",
    "duration_ms": 420,
    "summary": "..."
  }
}
```

SSE 的 `event:` 可以继续使用 type，但 `data` envelope 保持完整。

要求：
- `seq` 对单 run 单调；
- replay 使用原 sequence；
- live/replay 同一事件 schema；
- 终止事件明确；
- unknown fields 向前兼容；
- client schema_version 不支持时给升级提示。

---

## 7. Reconnect

P1 后：
- 使用 Last-Event-ID 或 `after_seq`；
- dedupe `(run_id, seq)`；
- exponential backoff 有上限；
- run terminal 后不 reconnect；
- 401 不重试；
- 404 不重试；
- 5xx/EOF 可重试。

P0 若 server 不支持 sequence：
- 不自动无限 reconnect；
- 发生异常后 GET run；
- 若 run terminal -> replay；
- 若仍 running -> 明示 reconnect。

---

## 8. Render throttle

模型 token 可能高频到达。
不要每 delta 强制全屏重排。

建议：
- transport 全速读取；
- reducer 接收；
- UI 16–50ms coalesce assistant delta；
- tool/approval/error 事件立即 flush；
- final 必须 flush。

这解决“网络流速度”和“UI 刷新速度”解耦。

---

## 9. Headless JSONL

不要直接透传 ServerEvent，定义 client public schema：

```json
{"v":1,"type":"run.started","run_id":"..."}
{"v":1,"type":"tool.started","tool":{"name":"filesystem.read"}}
{"v":1,"type":"assistant.delta","text":"..."}
{"v":1,"type":"run.completed","result":{"text":"..."}}
```

稳定性策略：
- 新增字段兼容；
- 改名/删除字段升 major schema；
- stdout 每行一个 JSON；
- stderr 可有人类日志；
- `--quiet` 抑制 stderr 非错误输出。
