# 003 — 工具循环 bug：当前状态与交接

> **主题**：Agent 反复调用同一工具的循环 bug —— 已做修复 + 仍需处理的模型侧问题
> **日期**：2026-08-12
> **分支**：`cli-v2-upgrade`（本地工作树 `composed-giggling-ocean`，已推送）
> **接手 Agent 必读**：本文件 → `001-cli-v2-upgrade-report.md`（架构）→ `002-adversarial-review-progress.md`（对抗审查全部完成）

---

## 1. 一句话交接

我们的 Agent 是 **ReAct 式单 Agent 循环**（`decide → tool → observe → decide`）。用户实测：问"帮我看看原神最新版本 / 帮我看看文件"时，模型**连续 5 次重复调用 `http_fetch.fetch` / `filesystem.list`**，触发 `MAX_TOOL_CALLS=5` 保险丝后被迫放弃，并声称"无法调用工具"（其实每次调用都成功）。**循环不收敛**。

## 2. 已定位并修复的 3 个根因（都在服务端，需重启 API 生效）

| 根因 | 修复 | 提交 |
|---|---|---|
| **P0-001 上下文陈旧**：`observe` 追加工具结果后 `decide` 复用第一次建的 `_cached_context`，模型根本看不到结果 | 所有改 messages/context 的节点返回 `_cached_context=None` 强制重建 | `c1649cc` |
| **review-§B 相同调用重发**：模型看到结果仍发出**完全相同**的调用 | `tool_request` 用 `tool_name+规范化参数` 签名，命中此前成功调用则不执行 connector，返回 `TOOL_REPEAT_BLOCKED` 提示"已成功请勿重复" | `1dd681c` |
| **循环窗口丢用户指令**：ContextEngine `kept[-10:]` 硬限 10 条，循环消息超 10 条后**丢掉原始 user 消息**，模型无指令可收敛 | `_tier3_conversation` 始终保留最后一条 user 消息，窗口改由 token 预算（~5440）决定、上限 30 条 | `5e361ac` |

**诊断证据**（真实 run，重启后复测可复现）：
- run `47a24bba`：5 次 `http_fetch.fetch` **全部成功**返回官网标题（`《原神》官方网站-全新7.0版本…`），模型仍换 URL 继续抓——搜索式探索烧光 5 次。
- run `7674bc00`：5 次 `filesystem.list` **全部成功**（`25 entries` / `2257 entries`），其中 `recursive=true` 连续 **3 次完全相同**。
- 结论：修复前模型"看不到结果"，修复后"看得到结果但仍重发"——前两个根因是代码问题，最后一个涉及模型行为。

## 3. 现状：全部 47 项对抗审查已执行 + 3 个循环守卫已上线

- `pytest`：**532 通过**，`ruff` 干净。
- 架构：`docs/agent-memory/001`、`002` 记录了完整背景与所有已做修复的位置。
- Alembic baseline + 迁移已就位；一键启动：`.venv\Scripts\python.exe scripts\dev.py`（迁库→起 API→起 TUI）。

## 4. 仍未解决的问题（交给你的核心任务）

**重启后（代码修复已生效），模型对 `TOOL_REPEAT_BLOCKED` 的遵循度仍可能不高**——它可能看到"已成功请勿重复"后仍发第三次/第四次（不同参数、或干脆再发相同）。当前 `MAX_TOOL_CALLS=5` 只是保险丝，不是收敛机制。

建议的排查/修复方向（按优先级）：

1. **在 `decide` 的提示词层主动预置防重复指令**：当本轮已有成功工具结果时，在发往模型的 `messages` 里追加一条 system 指令（如"你已成功调用过 X，禁止重复相同调用；基于已有结果直接回答，或用 search/不同参数"），让模型在**决策前**就知道不能重复，而不是等 `tool_request` 事后拦截。
   - 位置：`src/personal_ai_os/agent_runtime/graph.py` 的 `decide`（在拼 `messages` 处，参考现有 `MAX_TOOL_CALLS` 的 system 注入段）。
2. **收敛诊断**：在 run 里记录"重复调用被拦次数"（`TOOL_REPEAT_BLOCKED` 次数）与最终原因，方便判断是模型行为还是仍有代码缺口。
3. **如果模型对提示语仍不遵循**：考虑在 `decide` 里，检测到"最近连续 N 次都是工具调用且无进展"时，直接强制 `pending_response`（不让模型再选工具），把控制权收回——比 `MAX_TOOL_CALLS` 更早、更温和。
4. **可选**：给 `http_fetch`/`filesystem` 的模型可见输出再精简（`_preview_tool_data` 已把大结果截到 40 项 + 截断提示），确认不是"结果太大把用户/指令挤出预算"——预算默认 16000 token，`conversation+tool_results` 合计 ~5440，大网页内容可能吃光。

## 5. 接手验证基线

```bash
cd .claude/worktrees/composed-giggling-ocean
.venv\Scripts\python.exe -m pytest -q          # 期望 532 passed
.venv\Scripts\ruff.exe check src apps connectors tests
```

复现路径：`.venv\Scripts\python.exe scripts\dev.py` 起服务 → TUI 里问"帮我看看原神最新版本内容" → 观察是否只 fetch 一次就回答。

## 6. 硬性提醒

- 服务端改动必须**重启 API**（`scripts\dev.py` 一键重启）才生效。
- 不要动 CLI 侧的 `_ensure_utf8` 顺序（Windows GBK 问题）。
- 新 DB 改动走 Alembic（`migrations/versions/`），不要只靠 `create_all`。
- `uv sync` 可能因运行中的 `personal-ai.exe` 文件锁失败——依赖已装好，忽略即可。
