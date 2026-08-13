# 06 — TUI 视觉与组件规范

## 1. 视觉目标

关键词：
- quiet
- operational
- readable
- trustworthy
- progressive

不要：
- 大面积边框；
- 过度 emoji；
- 彩虹色；
- 每个 token 都触发复杂重排；
- 只靠颜色表达风险。

---

## 2. 语义 token

不要在业务代码写固定 RGB。

```text
text.primary
text.muted
text.subtle
accent
success
warning
danger
info
border
surface.raised
diff.add
diff.remove
risk.r0 ... risk.r4
```

至少提供：
- auto dark/light
- high contrast
- no-color

---

## 3. 状态符号

必须有 ASCII fallback：

| 状态 | Unicode | ASCII |
|---|---|---|
| running | `●`/spinner | `*` |
| completed | `✓` | `OK` |
| failed | `✕` | `ERR` |
| waiting | `!` | `!` |
| tool | `›` | `>` |

不要让 emoji 宽度影响布局。

---

## 4. Transcript cell

### Tool running
```text
› filesystem.read  reading src/...
  running · 1.8s
```

### Tool completed
```text
✓ filesystem.read  126 lines
  420 ms
```

### Tool failed
```text
✕ http_fetch  request failed
  connection timeout · press Ctrl+O for details
```

### Approval
```text
! Approval required · R3 external side effect
  email.send
  To: ...
  Action: Send message to external recipient

  [A] Approve once   [E] Edit   [R] Reject   [C] Cancel run
```

---

## 5. Markdown

Final assistant text支持：
- headings
- lists
- code fences
- tables（窄屏降级）
- links（OSC8 仅 capability 支持时）
- inline code

Streaming markdown：
- 不应每 token 完整 parse；
- 采用 chunk buffering；
- incomplete code fence 保持 plain/temporary；
- final event 时 full render。

---

## 6. 宽度断点

### < 72
- 不显示 header table；
- status 仅 `session | model | state`；
- tool args 只一行；
- table 转 vertical key/value；
- approval actions 可换行。

### 72–119
- 标准模式。

### >= 120
- 可在 overlay 中使用双栏；
- 默认 transcript 仍保持可读最大宽度，不无限拉伸。

---

## 7. 高度
低高度时优先保留：
1. composer
2. approval action
3. last transcript
4. status
5. help hints

---

## 8. 动画
- spinner <= 10 fps；
- respect reduced-motion config；
- 非 TTY 无动画；
- animation 不改变行高。

---

## 9. Copy / raw mode
提供：
- `/copy` 或 keybind copy last answer；
- `/raw`/transcript pager（可后续）；
- tool output 可复制；
- 不把 ANSI 控制符复制进文本。

---

## 10. Terminal title
可选：
`Personal AI — <session title> [waiting approval]`

任何来自 model/session 的内容输出到 OSC 前先 sanitize 控制字符。
