"""One-click dev startup: migrate DB → start API (new window) → open TUI.

Usage:
    .venv\\Scripts\\python.exe scripts\\dev.py

Windows only (uses CREATE_NEW_CONSOLE for the API). Stops the API when the TUI
exits. If an API is already listening on :8000 it is reused.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
API_URL = "http://127.0.0.1:8000"


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([ROOT, os.path.join(ROOT, "src")])
    return env


def _api_alive() -> bool:
    try:
        urllib.request.urlopen(f"{API_URL}/healthz", timeout=1)
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    os.chdir(ROOT)
    print("=" * 60)
    print("Personal AI OS — 一键开发启动")
    print("=" * 60)

    # 1. migrate the local SQLite dev DB (best-effort; fresh DBs are created by
    #    create_all on app startup, but existing DBs need the new columns).
    print("\n[1/3] 数据库迁移 (alembic upgrade head)…")
    db_file = os.path.join("data", "app.db")
    env = _env()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_file}"
    try:
        subprocess.run(
            [VENV_PY, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT, env=env, check=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    迁移失败（可忽略，若为全新库）: {exc}")

    # 2. start the API in its own console window.
    api: subprocess.Popen | None = None
    if _api_alive():
        print("    API 已在运行，复用现有实例。")
    else:
        print(f"\n[2/3] 启动 API 服务 → {API_URL}（新窗口）…")
        api = subprocess.Popen(
            [VENV_PY, "-m", "apps.api.main"],
            cwd=ROOT, env=_env(),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        print("    等待服务就绪…")
        for _ in range(90):
            if _api_alive():
                break
            time.sleep(1)
        if not _api_alive():
            print("    API 未在预期时间内就绪，请查看 API 窗口输出。")

    # 3. open the interactive TUI in this window.
    print("\n[3/3] 启动交互 TUI（输入 Ctrl+C 退出）…\n")
    tui = [os.path.join(ROOT, ".venv", "Scripts", "personal-ai.exe")]
    if not os.path.exists(tui[0]):
        tui = [VENV_PY, "-m", "apps.cli.main"]
    try:
        subprocess.call(tui, cwd=ROOT, env=_env())
    except KeyboardInterrupt:
        pass
    finally:
        if api is not None and api.poll() is None:
            api.terminate()
        print("\n已退出。API 服务已停止。")


if __name__ == "__main__":
    main()
