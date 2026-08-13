# 18 — Agent Task Cards

> 每张卡可独立交给 Coding Agent。不得把多张卡合并成“一次完成全部”。

---

## T00 — Baseline inventory
**Read**: README, pyproject, apps/cli/main.py, docs/API.md, docs/ARCHITECTURE.md  
**Do**:
- 记录现有 commands/flags/env；
- 记录 API endpoints/events；
- 记录 package/install 路径。
**Change**: docs/tests only.  
**Acceptance**: baseline markdown + no runtime behavior change.

## T01 — Packaging smoke
**Do**:
- build wheel；
- clean venv install；
- 确认 `personal-ai` entry point；
- 若缺失，添加正确 `[project.scripts]` 与 package inclusion。
**Acceptance**: installed wheel can `personal-ai --help`.

## T02 — Legacy CLI contract tests
**Do**:
- 给旧 chat/send/sessions/config/approve 建行为 tests；
- mock HTTP。
**Acceptance**: tests fail only when public behavior changes.

## T03 — Stream fixtures
**Do**:
- 保存 live/replay/approval/tool SSE fixtures。
**Acceptance**: fixture semantics documented.

---

## T10 — Async API client
**Files**: new `cli/api/client.py`, `errors.py`, `dto.py`  
**Do**:
- async httpx；
- typed error；
- no print/exit；
- all current API paths。
**Acceptance**: HTTP status mapping tests.

## T11 — SSE decoder
**Do**:
- standards-compliant event parsing；
- multiline；
- comments；
- EOF；
- cancellation。
**Acceptance**: all SSE fixtures pass.

## T12 — UIEvent normalization
**Do**:
- legacy server events -> stable UIEvent；
- suppress raw thinking default；
- tool/approval/run mappings。
**Acceptance**: live/replay normalize to equivalent semantics.

## T13 — Reducer/AppState
**Do**:
- run/tool/approval/transcript connection state；
- pure reducer tests。
**Acceptance**: fixture replay produces deterministic final state.

## T14 — Headless renderers
**Do**:
- text/json/stream-json；
- stdout/stderr policy；
- exit code mapping。
**Acceptance**: contract tests.

## T15 — Command shell
**Do**:
- Typer root；
- map old command；
- new `exec`；
- do not start TUI in non-TTY automation accidentally。
**Acceptance**: help snapshot and aliases.

---

## T20 — TUI framework spike
**Do**:
- Textual POC；
- SSE append；
- 10k deltas；
- resize；
- Windows；
- inline/alternate；
- snapshots。
**Output**: ADR-001 decision.
**Acceptance**: explicit go/no-go.

## T21 — App shell
**Do**:
- bootstrap；
- connection state；
- status bar；
- transcript/composer areas。
**Acceptance**: app starts before network health completes.

## T22 — Composer
**Do**:
- multiline；
- history；
- completion；
- queue/disable behavior active run。
**Acceptance**: keyboard tests.

## T23 — Transcript cells
**Do**:
- user/assistant/tool/error/notice/progress typed cells。
**Acceptance**: width snapshots.

## T24 — Streaming renderer
**Do**:
- coalesced deltas；
- final markdown；
- tool immediate events。
**Acceptance**: no UI starvation at high delta rate.

## T25 — Status/key hints
**Do**:
- session/model/run/connection；
- contextual hints。
**Acceptance**: no color-only status.

## T26 — Responsive/accessibility
**Do**:
- width breakpoints；
- NO_COLOR；
- reduced animation；
- screen reader/plain fallback。
**Acceptance**: snapshots + manual checklist.

---

## T30 — Session picker
**Do**:
- list；
- fuzzy search；
- current project priority；
- --continue；
- --session selector。
**Acceptance**: no UUID required common path.

## T31 — Approval view
**Do**:
- risk/action/args；
- approve/reject/edit/cancel actions。
**Acceptance**: no secret/ANSI injection.

## T32 — Approval resolution + resume
**Do**:
- POST decision；
- POST resume；
- reconnect stream；
- preserve transcript。
**Acceptance**: full waiting->approve->complete integration test.

## T33 — Tool detail
**Do**:
- requested/running/completed/failed；
- elapsed；
- details pager。
**Acceptance**: tool failure visible.

## T34 — Memory UX
**Do**:
- list/search/show/forget；
- TUI overlay。
**Acceptance**: forget confirmation and provenance display.

## T35 — Status/context
**Do**:
- `/status`；
- `/context` summary；
- unknown fields explicit。
**Acceptance**: no system prompt/secret dump.

## T36 — Admin resources
**Do**:
- tools/audit commands；
- JSON outputs。
**Acceptance**: table + JSON tests.

---

## T40 — Runs list API
**Server**:
`GET /v1/runs?session_id=&status=&limit=&cursor=`
**Do**:
- owner-scoped；
- pagination；
- tests；
- docs。
**Acceptance**: CLI removes session-active-run workaround.

## T41 — Event envelope v1
**Server**:
- schema_version
- event_id/seq
- ts
- run_id
- data
**Do**:
- live/replay same；
- legacy compatibility。
**Acceptance**: contract tests.

## T42 — Tool lifecycle event identity
**Do**:
- stable tool_call_id every event；
- requested/started/completed/failed。
**Acceptance**: reducer never matches by tool name.

## T43 — Approval event enrichment
**Do**:
- run_id
- approval_id
- tool_call_id
- risk/action summary
**Acceptance**: TUI no global pending-list guess for active approval.

## T44 — Session list metadata
**Do**:
- title/status/updated/active_run/model/project/turn summary as appropriate。
**Acceptance**: selector no N+1 requests.

---

## T50 — Doctor
**Acceptance**: actionable checks, zero secret disclosure.

## T51 — Config sources
**Acceptance**: source precedence test.

## T52 — Terminal sanitizer
**Acceptance**: ANSI/OSC attack fixtures.

## T53 — Notifications
**Acceptance**: opt-in, no sensitive args.

## T54 — Performance
**Acceptance**: defined stress fixtures pass.

## T55 — Release
**Do**:
- docs；
- changelog；
- migration note；
- wheel；
- shell completion if supported。
**Acceptance**: clean install on 3 OS where CI available.
