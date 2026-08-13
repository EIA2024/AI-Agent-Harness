# ADR-001 — TUI Framework

## Status
Proposed; Phase T20 validates.

## Context
项目是 Python 3.12，现有 CLI 是 stdlib argparse + httpx。目标需要：
- async SSE
- rich transcript
- modal/pickers
- input history/completion
- keymap
- responsive resize
- snapshot tests
- cross-platform.

## Decision
首选 **Typer + Textual + Rich**：
- Typer 负责 CLI command surface；
- Textual 负责 interactive app；
- Rich 负责 Markdown/syntax/table。

TUI 不能包含核心 domain/network 逻辑，因此若 framework 更换，不影响 headless。

## Alternatives
### prompt_toolkit + Rich
优点：scrollback/REPL 心智更自然，input 强。
缺点：复杂多 widget、overlay、snapshot、async rendering 需要更多自建协调。

### curses
不选：跨平台、unicode、测试、组件化成本高。

### 保留 print/input
不选：无法满足状态型 UI。

## Go/No-Go Spike
验证：
- 10k delta
- resizing
- inline/alternate
- Windows
- clipboard/selection
- snapshot
- no-color
- startup latency

失败则选择 prompt_toolkit + Rich，但保留相同 architecture。
