# 007 - 安全修复与本地启动收尾

> 日期：2026-08-13
> 分支：`main`
> 基线：`a080016`
> 状态：已本地验证，待推送

## 本轮现役变更

- 审批恢复必须携带绑定到当前 Run 的 `approval_id`；R4 的 passkey 要求会从 Policy 经 Broker、Graph、持久化审批记录与 CLI 传递，缺少验证器时保持 fail-closed。
- 工具生命周期事件在 connector 真正开始执行前才发送 `tool.started`，审批等待阶段不会错误标记为已启动。
- 文件系统连接器限制目录枚举数量和写入字节数，过滤敏感文件，并在 macOS 使用已打开文件描述符验证路径。
- HTTP 连接器拒绝模型提供的凭据、路由和 hop-by-hop 请求头；固定连接仍以原始目标主机作为 `Host` 与 SNI。
- API 输入收紧了 Session、Memory 与恢复请求的枚举和数值边界；生产环境必须显式提供 `DATABASE_URL`。
- `scripts/smoke.sh` 使用临时数据库和临时密钥，等待 API/Run 就绪并在失败时输出服务日志。

## 本地启动

- macOS/Linux：`./start.sh`
- Windows：`start.bat`
- 两个入口均委托 `scripts/dev.py`：迁移本地 SQLite、启动 API、进入 TUI；首次开发启动会在被忽略的 `.env` 中生成本地 API 密钥。

## 验证

- `uv run pytest -q`：632 passed。
- `uv run ruff check src apps connectors tests scripts`：通过。
- `git diff --check`：通过。

## 遗留

- Windows 批处理入口仅做静态检查；本轮 macOS 环境无法执行 `start.bat`。
- `.trae/` 是本地会话规格产物，已加入 `.gitignore`，现有文件等待确认后再物理删除。
