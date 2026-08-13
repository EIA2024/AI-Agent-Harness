# 16 — Migration / Backward Compatibility

## 1. 原则
CLI v2 可以变好，但不能突然让现有脚本失效。

---

## 2. 兼容映射

| 旧 | 新 | 策略 |
|---|---|---|
| `personal-ai chat` | `personal-ai` | 保留 alias |
| `send TEXT` | `exec TEXT` | 1–2 minor version deprecation |
| `runs get ID` | `runs show ID` | alias |
| `approve --list` | `approvals list` | alias |
| `approve ID` | `approvals approve ID` | alias |
| `--no-thinking` | 默认不显示 raw thinking | 接受 flag但提示无必要 |

---

## 3. Deprecation
human stderr：
```text
Warning: `personal-ai send` is deprecated; use `personal-ai exec`.
```

JSON stdout 不允许出现 warning。
warning 走 stderr。

---

## 4. Config migration
Provider profile schema：
- 读取旧 profiles；
- 写新字段时 schema version；
- upgrade 前备份；
- migration idempotent；
- 不复制 secret 到 project config。

---

## 5. Entry point migration
当前 packaging 需要验证 apps/cli 是否进入 wheel。

实施：
1. build wheel；
2. 新 venv；
3. install wheel；
4. `personal-ai --help`；
5. `personal-ai doctor`；
6. import check。

---

## 6. API compatibility
P1 event envelope：
- server 暂时支持 legacy SSE；
- client 支持 legacy + v1；
- feature detection；
- 至少一个 release window 后再移除 legacy。
