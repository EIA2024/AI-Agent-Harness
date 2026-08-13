# AI-Agent-Harness 对抗性代码审查与修复计划

- Repository: `https://github.com/EIA2024/AI-Agent-Harness`
- Branch: `cli-v2-upgrade`
- Reviewed tree SHA: `4db7ba12fd1e4c09e4b9539dc0850345c72e96ad`
- Review date: 2026-08-12
- 目标执行模型: DeepSeek（因此任务卡故意写得冗余、显式、可验证）

> 结论分级：`VERIFIED` 表示可由当前分支代码直接证明；`PROJECTED` 表示当前设计在指定扩展条件下高概率出问题。不要把 PROJECTED 当成线上已发生事故。
> 本包是 adversarial review，不承诺数学意义上的“所有 bug”，但覆盖了当前可见主链：API → Runner → LangGraph → ToolBroker/Policy/Approval → Connectors → Context/Memory → DB → SSE/CLI → Deploy。

## Connector / Sandbox 专项\n\n### HTTP\n**立即拆分 capability**：\n- `http_fetch.read`: GET/HEAD, R1, no credentials by default.\n- `http_request.mutate`: POST/PUT/PATCH/DELETE, R3 或更高，external_write=True, side_effect=True, idempotency 根据 method+endpoint 单独判断。\n\nSSRF 不能只在请求前 DNS 检查。连接必须 pin 已验证 IP 或连接后核验 peer IP；显式阻断 loopback/private/link-local/unspecified/reserved/metadata；生产建议 allowlist 默认 deny。\n\nHeader policy：禁止 Agent 任意构造 `Host`, `Proxy-Authorization`, hop-by-hop headers；Authorization 必须通过 credential channel，不应由 LLM 原文生成。\n\n### Filesystem\n1. Root：prod 明确 `PERSONAL_AI_WORKSPACE_ROOT`，禁止 cwd 偶然决定。\n2. IO：所有同步 scan/read/write 转线程池/隔离 worker。\n3. Search：默认 literal；regex 需 deadline/safe engine。\n4. Budget：max_depth、max_entries、max_files_scanned、max_bytes_scanned、max_results 全部 hard clamp。\n5. Cycle：visited `(st_dev, st_ino)` / canonical path。\n6. Secret：以 workspace capability 为主，不依赖文件名 denylist。\n7. Write：需要原子 temp+rename；必要时 no-follow/openat 防 symlink race。\n8. Result：分页/cursor + artifact，不返回全部 data。\n\n### Registry CapabilityValidator\n注册 connector 时检查元数据不变量，而不是相信作者：\n- `destructive=True => risk>=4`\n- `external_write=True or side_effect=True => risk>=3`（如产品明确允许本地 workspace write，可定义 LocalWrite capability，不能混成 generic external_write）\n- `risk<=1 => side_effect=False && destructive=False && external_write=False`\n- mutate HTTP methods 不能出现在 read descriptor\n- credential scope 必须声明 auth boundary\n\n### 临时回滚\n如果来不及完成 sandbox：prod feature flag 禁用 `http_request.mutate` 与 `filesystem.write/search(regex)`；不要降低 approval。\n