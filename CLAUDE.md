# AI Agent Harness — 工作区指引

本仓库是一个长期运行的个人 Agent 运行时（Personal AI OS）：LangGraph + FastAPI，含持久化会话、工具/审批/记忆/审计。结构总览见 `docs/ARCHITECTURE.md`；用户向 CLI 文档见 `docs/cli-v2.md`。

## 项目记忆路由

**项目的过程记忆（历史记录、决策过程、升级报告）不放在本文件。** 它们存放在：

> `docs/agent-memory/`（索引见该目录 `README.md`）

- 开始涉及 CLI / API / 运行时架构演进或相关修复前，先通过 `docs/agent-memory/README.md` 查找并读取相关记录。
- 完成一段有长期价值的工作后，按 `docs/agent-memory/README.md` 的约定追加一条编号记录并更新索引。
- 本文件只写**整个工作区必须遵循的硬性约束**；过程事实一律进记忆库。

## 工作区硬性约束

- **CLI / API / transport 层不 print、不 SystemExit**——只 return value / yield event / raise typed exception；渲染由上层负责。
- **服务端 Policy Engine 是权限真源**。CLI 是 UI 不是授权引擎；审批 approve/reject/edit 必须走服务端 API（含 hash-binding / resume）；禁止在客户端模拟自动批准。
- **默认不向用户展示 raw chain-of-thought**（`thinking.delta` 只作为受控内部数据）。
- **所有 terminal 输出防 ANSI/OSC/control-sequence 注入**（见 `src/personal_ai_os/cli/sanitize.py`）。
- **headless stdout 是契约**：`exec` 的 stdout 只含结果协议，进度/诊断走 stderr；退出码稳定（见 `src/personal_ai_os/cli/output/exit_code.py`）。
- **交互与 headless 共用 transport/domain，不共用 presentation**——CI 永不依赖 TTY。
- **Windows 编码**：Rich/Unicode 输出在 GBK 控制台会失败，任何 CLI 入口都必须在解析前 `sys.stdout/stderr.reconfigure(encoding="utf-8")`。
- **Provider 密钥只进 OS Keyring**：Profile metadata 可写本地配置，API Key 不得明文落盘、进入 API 响应或终端输出。

## 工具

- 测试：`uv run pytest -q`（或 Windows：`.venv/Scripts/python.exe -m pytest -q`）
- Lint：`uv run ruff check src connectors apps tests`
- 门禁：两者全绿。
- 升级方案/任务卡：`CLI-Upgrade-Plan/`（执行协议见 `00_EXECUTION_PROTOCOL.md`）。
