# 19 — 最终验收清单

## Architecture
- [ ] `main.py` 不再包含网络、SSE、render、config 全部逻辑
- [ ] API client 无 print/SystemExit
- [ ] TUI components 无直接 HTTP
- [ ] live/replay 都通过 normalization
- [ ] TUI/headless 共用 transport/domain

## Interactive
- [ ] `personal-ai` 能进入交互 UI
- [ ] first paint 不等待 provider 请求
- [ ] multiline input
- [ ] streaming assistant
- [ ] tool requested/running/completed/failed
- [ ] session resume
- [ ] approval approve/reject/edit
- [ ] run cancel
- [ ] help/status
- [ ] resize

## Headless
- [ ] `exec`
- [ ] text
- [ ] json
- [ ] stream-json
- [ ] stdout/stderr 分离
- [ ] stable exit codes
- [ ] non-TTY no animation
- [ ] approval required 不偷偷批准

## Security
- [ ] server policy is source of truth
- [ ] secrets redacted
- [ ] tool terminal escape sanitized
- [ ] OSC title sanitized
- [ ] approval edit goes through server binding
- [ ] R4 visible in text and semantic label
- [ ] no raw chain-of-thought by default

## Session/Memory
- [ ] no-UUID common resume
- [ ] memory list/search/forget
- [ ] context summary does not dump system secrets
- [ ] compaction visible when available

## Reliability
- [ ] EOF handling
- [ ] 401
- [ ] 404
- [ ] 409 double approval/resume
- [ ] 5xx
- [ ] reconnect/replay
- [ ] Ctrl+C active run
- [ ] SIGPIPE

## Accessibility
- [ ] NO_COLOR
- [ ] ASCII glyph fallback
- [ ] 60/80/120/160 widths
- [ ] keyboard-only
- [ ] screen reader/plain fallback
- [ ] reduced animation

## Testing
- [ ] SSE fixtures
- [ ] reducer unit
- [ ] output contract
- [ ] UI snapshots
- [ ] approval integration
- [ ] packaging smoke
- [ ] ruff
- [ ] pytest
