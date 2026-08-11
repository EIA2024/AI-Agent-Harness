# 09 — Approval / Policy / Security UX

## 1. 核心原则

**CLI 不能成为新的权限决策边界。**

服务端已有 ToolBroker + Policy + Approval + hash binding。
CLI 的责任：
- 显示；
- 收集用户决策；
- 调 API；
- 显示结果。

---

## 2. Risk presentation

R0–R4 全部既用文字又用颜色：

```text
R0 · compute
R1 · read
R2 · local write
R3 · external side effect
R4 · destructive / credential-sensitive
```

不要只显示“红色”。

---

## 3. Approval Card 最小字段

- risk
- tool_name
- action_summary
- arguments preview（敏感字段 redacted）
- target/domain/path/recipient 等关键实体
- “what changes”
- “data leaves device?”（若 descriptor 能判断）
- approval id（details 中）
- run/session short id

---

## 4. 操作

### Approve once
只针对该 exact request。

### Edit
打开 JSON/schema-aware form：
- 原参数；
- JSON schema 类型；
- 编辑后的 diff；
- 走 `/edit` / resume；
- 后端重新 hash。

### Reject
默认含义：
- reject 当前 action；
- runtime 获得 rejected observation 并决定后续。
不能等价于 kill process，除非服务端逻辑如此。

### Cancel run
显式调用 run cancel。

---

## 5. 自动权限模式（P2）

不要在 P0 做：
```python
if yolo:
    POST approve every pending
```

原因：
- 客户端可被绕过；
- R4 失去保护；
- audit 语义不清；
- 后端仍认为是人工批准。

正确做法：
- server 接收 `approval_mode`；
- Policy Engine 根据 mode + risk + scope 决策；
- audit 标记 `auto_policy` 而不是 `human`;
- R4 可始终要求 strict approval；
- CLI 只选择 mode。

---

## 6. Approval notification
如果 TUI 不聚焦：
- terminal bell/OSC notification 可选；
- 不在消息中泄露敏感 args；
- 通知只写 “Personal AI requires approval”。

---

## 7. 安全输出
所有 presentation 经过：
- secret redactor；
- control-char sanitizer；
- OSC sanitizer；
- path/url truncate；
- untrusted terminal escape stripping。

尤其 tool output 可能含 ANSI/OSC，**不得原样写到 terminal**。

---

## 8. 安全测试
必须包含：
- malicious OSC title；
- ANSI cursor move；
- secret string；
- approval args hash mismatch；
- edit args；
- double approve 409；
- concurrent resume；
- cancelled run；
- R4 UI 强提醒。
