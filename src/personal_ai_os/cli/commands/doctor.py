"""``personal-ai doctor`` — environment diagnostics (CLI v2, T50 baseline).

Checks run locally-first, then against the API. Never prints secrets — API keys
are reported as configured/missing only.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TextIO

from personal_ai_os.cli import __version__
from personal_ai_os.cli.api.errors import APIError, TransportError
from personal_ai_os.cli.bootstrap import api_key, api_url, build_client
from personal_ai_os.model_gateway import ProviderConfigStore


def _line(status: str, name: str, message: str) -> str:
    return f"{status:6} {name:<10} {message}\n"


async def diagnose(stdout: TextIO) -> None:
    stdout.write(_line("OK", "CLI", f"personal-ai {__version__}"))
    try:
        from importlib.metadata import version as pkg_version

        stdout.write(_line("OK", "Package", pkg_version("personal-ai-os")))
    except Exception:  # noqa: BLE001
        stdout.write(_line("WARN", "Package", "not installed as a distribution"))

    store = ProviderConfigStore()
    profiles = store.list_profiles()
    active = store.get_active_name()
    if active:
        stdout.write(_line("OK", "Provider", f"{active} ({len(profiles)} profiles)"))
    else:
        stdout.write(_line("WARN", "Provider", "no active profile (demo/Echo mode)"))

    key = api_key()
    stdout.write(_line("OK" if key else "WARN", "Auth", "configured" if key else "missing API key"))

    url = api_url()
    stdout.write(_line("OK", "API URL", url))
    try:
        async with build_client() as client:
            health = await client.healthz()
        stdout.write(_line("OK", "API", f"reachable · {health.get('status', 'ok')}"))
    except (APIError, TransportError) as exc:
        stdout.write(_line("FAIL", "API", str(exc)))


def doctor_command() -> None:
    """Run environment checks."""
    asyncio.run(diagnose(sys.stdout))
