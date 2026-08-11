# 07 — 交互、命令与键盘规范

## 1. Composer

- Enter：提交；
- Ctrl+J：明确插入换行；
- Shift+Enter：终端支持时换行；
- Up/Down：输入为空时浏览 prompt history；
- Tab：slash/@ completion；
- Esc：关闭 popup / 清当前 completion；
- Ctrl+E：外部编辑器（P2）。

不要依赖 Shift+Enter 作为唯一多行方式。

---

## 2. Ctrl+C 状态机

Ctrl+C 必须按上下文处理：

### run active
第一次：
- 请求 `POST /runs/{id}/cancel`
- 状态栏显示 `Cancelling…`
- 不立即退出进程。

### no run + non-empty composer
清空当前输入（可配置）。

### idle + empty composer
第一次提示 `Press Ctrl+C again to exit`（短时间窗口），第二次退出。

避免误触导致整个 session 丢失。

---

## 3. Approval hotkeys

仅在 approval modal 聚焦：
- `a` approve once
- `e` edit arguments
- `r` reject
- `c` cancel run
- `o` details

所有 destructive action 都需要明确 label，不使用模糊 `y/n`。

---

## 4. 全局快捷键建议

| Key | Action |
|---|---|
| Ctrl+P | command palette |
| Ctrl+R | session/resume picker |
| Ctrl+O | expand focused/last tool detail |
| Ctrl+L | clear viewport（不清 Session） |
| Ctrl+G | cancel overlay |
| Ctrl+C | state-dependent interrupt |
| Esc | close overlay |
| ? | key hint overlay（composer 空时） |

必须允许 keymap 配置，防止终端冲突。

---

## 5. Slash command registry

定义统一 metadata：

```python
CommandSpec(
    name="status",
    description="Show session, model, context, and run state",
    availability=...,
    handler=...,
)
```

用于：
- popup；
- fuzzy filter；
- docs；
- keyboard palette；
- tests。

不要在 `if text.startswith("/...")` 中散落。

---

## 6. 状态感知命令

例如：
- `/cancel`：仅 active run 可用；
- `/approvals`：任何状态可打开列表；
- `/resume`：active run 时提示先 cancel/finish；
- `/new`：active run 时要求确认；
- `/compact`：服务端支持前隐藏或标 Experimental。

---

## 7. @ context（P2）

未来可以统一：
- `@file`
- `@memory`
- `@session`
- `@tool`
- `@artifact`

但 P0 不应假装支持不存在的上下文注入 API。
