# 12 — Headless / Automation / CI 契约

## 1. 命令

```bash
personal-ai exec "Summarize this project"
personal-ai exec - < prompt.txt
personal-ai exec "..." --session <id>
personal-ai exec "..." -o text
personal-ai exec "..." -o json
personal-ai exec "..." -o stream-json
```

旧 `send` 作为 alias/deprecation。

---

## 2. stdout / stderr

### text
stdout：
- final assistant answer only。

stderr：
- tool progress；
- session resume notice；
- warnings；
- errors（若没有 structured error mode）。

### json
stdout 单个 JSON object，run 完成后输出。

### stream-json
stdout JSONL events，实时。

绝不在 stdout 输出：
- spinner；
- ANSI status；
- banner；
- debug log。

---

## 3. Exit code

建议：

| code | meaning |
|---:|---|
| 0 | completed |
| 2 | CLI usage/config |
| 3 | auth |
| 4 | approval required in noninteractive policy |
| 5 | run failed |
| 6 | cancelled/interrupted |
| 7 | network/server unavailable |
| 8 | protocol/schema |

如果兼容历史脚本需要，先 ADR 再定。

---

## 4. 非交互 Approval

默认策略必须显式。

在当前服务端权限模型下：
- 如果 run waiting_approval，headless 不应该偷偷 approve；
- `text/json` 返回明确状态并 exit 4；
- JSONL 发 `approval.required` 后结束。

未来 server-side auto policy 上线后再加：
`--approval-mode auto-safe|...`

---

## 5. JSON result

```json
{
  "v": 1,
  "status": "completed",
  "session_id": "...",
  "run_id": "...",
  "output": {"text": "..."},
  "usage": null,
  "error": null
}
```

---

## 6. JSONL
每行完整合法 JSON，UTF-8：
```json
{"v":1,"type":"run.started",...}
{"v":1,"type":"tool.started",...}
{"v":1,"type":"assistant.delta","text":"..."}
{"v":1,"type":"run.completed",...}
```

不要把 model raw provider chunks 暴露成 public schema。

---

## 7. Pipe 行为
- stdin 非 TTY 且未传 prompt：从 stdin 读；
- prompt + stdin 同时存在：需要明确规则（建议 append `<stdin>` block，或强制 flag；写测试）；
- stdout 被 pipe：自动禁 color/animation；
- SIGPIPE 正常退出。

---

## 8. CI secret
API key：
- env；
- OS credential store（未来）；
- config secret reference；
- 不打印；
- `doctor` 只显示 `configured / missing`。
