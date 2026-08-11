# 11 — Tool / Diff / Artifact UX

## 1. Tool card 统一协议

每个 ToolViewState：
```text
id
name
risk
status
started_at
duration
summary
arguments_preview
result_preview
error
artifact_refs
```

Widget 只依赖这个模型，不依赖 connector 类型。

---

## 2. 默认折叠层级

### collapsed
`✓ filesystem.read  README.md · 126 lines · 420ms`

### expanded
显示：
- arguments（redacted）
- result preview
- timestamps
- tool_call id
- policy decision
- artifacts

### full pager
完整 sanitized output。

---

## 3. Streaming command output
如果未来加入 Bash/Shell：
- output 在 tool card 中 append；
- stdout/stderr visually distinct但不依赖颜色；
- 大输出 ring buffer；
- full output 写 artifact/log；
- TUI 只保留 tail；
- control sequence strip。

不要让 shell output 与 assistant answer 混在同一 stdout stream。

---

## 4. Filesystem write / diff
当前 connector 能力若扩展到 write/edit：
- approval 前展示 intended diff；
- completed 后展示 applied diff；
- summary：
  `2 files changed · +18 -4`
- binary/large file 显示 metadata，不 dump。

---

## 5. http_fetch
默认：
`✓ http_fetch  example.com · 200 · 42 KB`

details：
- final URL
- content type
- bytes
- truncation
- trust label
- duration

不要默认打印整页。

---

## 6. Artifact
数据库已有 artifacts 表概念，CLI P2 可建立：
- `/artifacts`
- save/export
- open path
- metadata

但先定义 API，不直接猜表结构。

---

## 7. Tool failure
显示：
```text
✕ filesystem.read — permission denied
  path: ...
  Agent can continue without this result.
```

若 failure 会导致 run failed，随后独立 RunStatusCell 解释。
