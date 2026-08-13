#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if command -v uv >/dev/null 2>&1; then
  exec uv run python scripts/dev.py
fi

if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python scripts/dev.py
fi

if [ -x ".venv/Scripts/python.exe" ]; then
  exec .venv/Scripts/python.exe scripts/dev.py
fi

echo "ERROR: 找不到 uv 或项目虚拟环境；请先安装 uv 并执行 'uv sync --extra dev'。" >&2
exit 1
