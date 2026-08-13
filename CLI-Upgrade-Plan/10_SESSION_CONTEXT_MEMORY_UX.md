# 10 — Session / Context / Memory UX

## 1. Session 是 CLI 的主导航对象

当前 CLI 创建/传 UUID 的方式太底层。

目标：
- default new/recent discovery；
- `--continue`；
- `--session` picker；
- title；
- cwd/project affinity；
- active/waiting status。

---

## 2. Session 状态
建议 display：
- title
- short id
- project/cwd
- updated
- turn count
- active run
- waiting approval
- model/profile

若 API 暂无某字段，不在客户端猜；Phase 4 扩 API。

---

## 3. `/status`

建议输出：

```text
Session    Memory retrieval debugging
Project    AI-Agent-Harness
Run        waiting_approval · 8a31...
Model      GLM-4.7 via z-ai
Policy     default · R3/R4 require approval
Context    recent 12 turns · summary present
Memory     3 relevant memories used this turn
API        connected · 42 ms
```

字段缺失时明确 `unknown/not reported`。

---

## 4. Memory commands

管理面：
- `memories list`
- `search`
- `show`
- `edit`
- `forget`

TUI：
- `/memory` overlay；
- 默认显示摘要和 scope；
- 详情显示 provenance；
- forget 二次确认（除非命令 flag `--yes` 明确）。

---

## 5. Context transparency

用户应能区分：
- system identity/policy（不显示 secret）；
- profile；
- skills；
- retrieved memories；
- conversation summary；
- recent turns；
- current tool results。

`/context` 显示 budget/来源摘要，而不是 dump 完整 system prompt。

---

## 6. Compaction
当 conversation summary 更新：
```text
· Context compacted — 18 older turns summarized
```

只显示一次 notice。
提供 `/context` 查看详情。

未来 `/compact` 必须走正式服务端 API，不在 CLI 本地删消息。

---

## 7. Fork / New
P2 可以支持 session fork：
- 复制 conversation/context reference；
- 新 session 独立 future；
- 明确 memory 是共享 store 还是 session-local。

在语义明确前不要实现。
