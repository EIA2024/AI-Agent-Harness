"""``personal-ai doctor`` — environment diagnostics (CLI v2, T50).

Checks run locally-first, then against the API. Never prints secrets — API keys
are reported as configured/missing only. Supports ``--json`` for automation.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
from dataclasses import dataclass, field
from typing import TextIO

from personal_ai_os.cli import __version__
from personal_ai_os.cli.api.errors import APIError, TransportError
from personal_ai_os.cli.bootstrap import api_key, api_url, build_client
from personal_ai_os.model_gateway import ProviderConfigStore


@dataclass
class Check:
    name: str
    status: str = "OK"  # OK | WARN | FAIL
    message: str = ""
    detail: dict = field(default_factory=dict)


def _line(status: str, name: str, message: str) -> str:
    return f"{status:6} {name:<10} {message}\n"


def _writable(path) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".doctor-probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


async def _checks() -> list[Check]:
    checks: list[Check] = []

    checks.append(Check("cli", message=f"personal-ai {__version__} · python {platform.python_version()}"))
    try:
        from importlib.metadata import version as pkg_version

        checks.append(Check("package", message=pkg_version("personal-ai-os")))
    except Exception:  # noqa: BLE001
        checks.append(Check("package", "WARN", "not installed as a distribution"))

    store = ProviderConfigStore()
    profiles = store.list_profiles()
    active = store.get_active_name()
    if active:
        checks.append(Check("provider", message=f"{active} ({len(profiles)} profiles)"))
    else:
        checks.append(Check("provider", "WARN", "no active profile (demo/Echo mode)"))

    checks.append(Check("auth", "OK" if api_key() else "WARN", "configured" if api_key() else "missing API key"))

    url = api_url()
    checks.append(Check("api-url", message=url))
    try:
        async with build_client() as client:
            health = await client.healthz()
        checks.append(Check("api", message=f"reachable · {health.get('status', 'ok')}"))
    except (APIError, TransportError) as exc:
        checks.append(Check("api", "FAIL", str(exc)))

    try:
        async with build_client() as client:
            tools = await client.list_tools()
        checks.append(Check("tools", message=f"{len(tools)} registered"))
    except (APIError, TransportError) as exc:
        checks.append(Check("tools", "WARN", str(exc)))

    from personal_ai_os.cli.tui.capabilities import color_enabled, is_tty, terminal_width

    term = "tty" if is_tty() else "pipe"
    term += f" · {terminal_width()} cols"
    term += "" if color_enabled() else " · NO_COLOR"
    checks.append(Check("terminal", message=term))

    config_dir = store.path.parent
    if _writable(config_dir):
        checks.append(Check("config-dir", message=str(config_dir)))
    else:
        checks.append(Check("config-dir", "FAIL", f"not writable: {config_dir}"))

    return checks


async def diagnose(stdout: TextIO, *, json_mode: bool = False) -> int:
    checks = await _checks()
    if json_mode:
        stdout.write(
            json.dumps(
                [{"name": c.name, "status": c.status, "message": c.message, "detail": c.detail} for c in checks],
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    else:
        for c in checks:
            stdout.write(_line(c.status, c.name, c.message))
    return 0 if all(c.status == "OK" for c in checks) else 1


def doctor_command(json_mode: bool = False) -> None:
    """Run environment checks; exit 1 when any check is not OK."""
    import typer

    code = asyncio.run(diagnose(sys.stdout, json_mode=json_mode))
    if code:
        raise typer.Exit(code=code)
