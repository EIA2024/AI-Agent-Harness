# 008 — Provider 运行时、TUI 配置与本地控制面

> **主题**：`add-tui-provider-model-setup`
> **日期**：2026-08-13 至 2026-08-17
> **分支/提交范围**：`main`（基于 `509b759` 的当前工作区，未提交）

## 实现状态

- `ProviderRuntimeService` 统一管理 Echo/真实 Provider 的公开状态、模型能力、模型枚举和热加载。
- `ReloadableProvider` 是 Runner、Graph、Classifier、Planner、Summarizer 共用的稳定引用；替换前先健康检查，失败保留旧实例。
- Runner 在一次 LangGraph 执行期间固定 Provider 快照，热加载只影响之后开始的 Run。
- 全局存在 `running` 或 `waiting_approval` Run 时拒绝 Provider/模型切换。
- DeepSeek、Anthropic 和未知兼容端点只声明 `auto`；明确识别的 OpenAI reasoning 模型声明 `auto/low/medium/high`。
- `ProviderProfile.reasoning_effort` 持久化到本地 metadata；API key 仍只从 OS Keyring 读取。
- 公开 Provider 身份从格式和 Base URL 推导，与用户自定义 Profile 名分离；例如 Profile `work` 可正确显示 Provider `deepseek`。

## API、TUI 与客户端

- `GET /v1/provider`
- `POST /v1/provider/reload`
- `GET /v1/provider/models`
- `PUT /v1/provider/model`

四个端点复用现有 `X-API-Key` 用户认证。写操作必须提交与服务端一致的 `config_dir`，否则返回 `412`，用于阻止远程或非共享配置目录误报成功。

- TUI 启动时读取 API 的真实 Provider 状态；无配置时保持 Echo 演示可用，并持续提示输入 `/api`。
- `/api` 使用非阻塞 Textual 界面新增、更新或切换 Profile；首次默认 DeepSeek。
- `/model` 枚举或手动选择模型，只展示当前 Provider 明确支持的思考强度。
- 配置成功后直接热加载，不退出 TUI，不重建 session，不清空 transcript 或长期记忆。
- API Key 输入遮罩；Keyring 不可用时 fail closed，不允许明文落盘。

## 验证

- 完整测试：665 passed；最终 Provider 状态回归测试 62 passed，端到端 1 passed。
- `uv run ruff check src connectors apps tests`：通过。
- 端到端覆盖 Echo 启动、TUI `/api`、认证 API、运行时热加载、`/model`、Runner/SSE、session/transcript 保留及后续请求使用新模型。
- 安全覆盖 Keyring fail closed、失败回滚、活动 Run 拒绝、远程目录边界、API key 防泄露，以及 DeepSeek 不发送不支持的 `reasoning_effort`。
