"""Personal AI OS command-line client package (CLI v2).

Package layout::

    main.py      — Typer root, argument parsing and dispatch only
    legacy.py    — the pre-v2 argparse CLI, kept as a compatibility path
    api/         — async HTTP client, DTOs, typed errors, SSE decoder
    domain/      — normalized UI events, AppState, reducer
    commands/    — headless / administrative subcommands
    controllers/ — side-effectful actions shared by TUI and commands
    output/      — text / json / JSONL renderers (headless)
    tui/         — interactive presentation (Textual)
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "2.0.0.dev0"
