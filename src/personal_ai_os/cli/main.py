"""Personal AI OS — CLI v2 entry point.

Phase 0: this module is a thin delegator to the legacy argparse CLI so the
``personal-ai`` console script works end-to-end and wheel packaging is
verified. Phase 1 (T15) replaces it with the Typer command tree; the legacy
commands are then provided as compatibility aliases.
"""

from __future__ import annotations

from personal_ai_os.cli.legacy import main as main

if __name__ == "__main__":
    raise SystemExit(main())
