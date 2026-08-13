# 17 — 分阶段实施路线

## Phase 0 — Baseline & Contracts

### 目标
不改变用户功能，建立可重构的安全网。

### Tasks
- T00 repository baseline
- T01 packaging smoke
- T02 CLI golden behavior
- T03 server API fixtures

### 修改
- `tests/unit/cli/*`
- `tests/integration/cli/*`
- `pyproject.toml`（仅测试/entry point 必要修改）

### 验收
- 现有 chat/send/config 行为被 test 描述；
- wheel 安装路径验证；
- API approval/run/stream fixture 可复用。

---

## Phase 1 — CLI Core

### 目标
把 transport/domain/headless 从 main.py 抽离。

### Tasks
- T10 AsyncAPIClient
- T11 SSE Decoder
- T12 UIEvent normalizer
- T13 reducer/domain
- T14 headless output
- T15 Typer command shell

### 关键约束
TUI 还没做好也没关系；`exec` 必须先可靠。

### 验收
- network layer 无 print/SystemExit；
- text/json/jsonl tests；
- raw thinking 不默认输出；
- unknown SSE 不 crash。

---

## Phase 2 — TUI MVP

### 目标
可替代 `input("> ")` loop。

### Tasks
- T20 framework spike/ADR
- T21 App shell
- T22 Composer
- T23 Transcript cells
- T24 streaming render
- T25 status/key hints
- T26 responsive/accessibility

### 验收
- user -> streaming answer；
- tool requested/completed；
- Ctrl+C cancel；
- resize；
- no color；
- screen reader/basic plain mode。

---

## Phase 3 — Control Plane UX

### 目标
把项目真正差异化能力暴露出来。

### Tasks
- T30 Session picker/resume
- T31 Approval modal
- T32 reject/edit/resume
- T33 Tool detail
- T34 Memory overlay/commands
- T35 status/context
- T36 audit/tools admin

### 验收
等待审批的 Run 无需离开 TUI 即可继续。

---

## Phase 4 — API Projection Improvements

### 目标
消除 client workaround。

### Tasks
- T40 `GET /v1/runs`
- T41 versioned SSE envelope
- T42 stable tool_call lifecycle
- T43 approval event enrichment
- T44 session list metadata

### 约束
保持 legacy API/event 兼容。

---

## Phase 5 — Production Hardening

### Tasks
- T50 doctor
- T51 config sources/provider picker
- T52 terminal sanitizer
- T53 notifications
- T54 performance
- T55 packaging/release/docs

---

## Phase 6 — Advanced Modes（独立决策）

只在前面完成后：
- explicit Plan Mode
- server-side approval modes
- background tasks
- artifact browser
- MCP management
- multi-agent display

每项单独 ADR + threat model。
