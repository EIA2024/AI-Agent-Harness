# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## Auth / Deployment / Multi-tenancy 专项\n\n### Compose 发布前必须改\n- 删除 `${PERSONAL_AI_DEV_API_KEY:-dev-key}`，改 `${PERSONAL_AI_API_KEY:?required}` 或正式 auth。\n- DB password 不允许 `pass` 默认。\n- PostgreSQL 删除 host `ports: 5432:5432`；仅 internal network。\n- API 若只供本机 CLI 默认 `127.0.0.1:${API_PORT}:8000`；远程通过 TLS reverse proxy/VPN。\n- 加 `APP_ENV=production`，启动检查弱口令/SQLite fallback/EchoProvider/CORS wildcard/InMemorySaver。任何一个出现都让 readiness fail，关键项直接退出。\n\n### API key\n数据库仅保存 prefix + hash。推荐随机 32 bytes；用户看到一次完整 key；请求先 prefix 找候选再 constant-time verify hash。支持 rotate/revoke。\n\n### Credentials\n当前 env secret 是 server-global。改成 `CredentialVault.get(owner_id, scope)`；单用户 mode 的 global env 也在启动时绑定到唯一 owner id，不能自动对未来新用户开放。\n\n### Tool visibility\nContextEngine 的 tools schema、`GET /v1/tools`、ToolBroker.execute 必须共享同一个 `CapabilityService`。不能 UI 不显示但 broker 仍可调，也不能 broker 允许但模型看不到。\n\n### CORS\nCLI 不是浏览器，不需要 CORS。默认关闭；Web UI 出现后使用精确 origin allowlist。\n