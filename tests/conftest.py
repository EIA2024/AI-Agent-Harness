"""Shared test configuration.

- Puts the worktree root (for ``connectors``) and ``src`` on ``sys.path``.
- Points the default DB at an in-memory SQLite so any stray session use in tests
  never attempts a PostgreSQL connection.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

WORKTREE_ROOT = Path(__file__).resolve().parent.parent

if str(WORKTREE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT))
if str(WORKTREE_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(WORKTREE_ROOT / "src"))

# Keep accidental db.session usage off PostgreSQL during tests.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
