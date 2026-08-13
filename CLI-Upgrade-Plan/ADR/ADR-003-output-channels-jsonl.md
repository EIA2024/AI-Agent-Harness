# ADR-003 — stdout/stderr 与 JSONL

## Decision
非交互：
- stdout = requested result protocol
- stderr = progress/diagnostic
- `stream-json` = versioned JSONL stdout

## Rationale
CLI 是 shell component。任何 human spinner/ANSI 混入 stdout 都会破坏自动化。

## Consequence
TUI renderer 与 headless renderer 必须完全分开。
