"""Personal AI OS command-line client (compatibility shim).

The real CLI lives in :mod:`personal_ai_os.cli` (inside the installable
package). This module keeps the historical ``python -m apps.cli.main``
invocation working.
"""

from __future__ import annotations

import sys

from personal_ai_os.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
