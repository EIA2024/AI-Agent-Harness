"""Run an async admin command with clean CLI error handling."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable

import typer

from personal_ai_os.cli.api.errors import APIError, TransportError
from personal_ai_os.cli.output.exit_code import ExitCode


def run_admin(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """Execute an async admin command; map API/transport failures to exit codes.

    Prevents raw tracebacks for expected failures (server down, 404 on an old
    server, auth errors) — the CLI prints a one-line error to stderr instead.
    """
    try:
        asyncio.run(coro_factory())
    except (APIError, TransportError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        code = _exit_code(exc)
        raise typer.Exit(code=code) from None
    except typer.Exit:
        raise


def _exit_code(exc: APIError | TransportError) -> int:
    if isinstance(exc, TransportError):
        return ExitCode.NETWORK_UNAVAILABLE
    if exc.status_code == 401:
        return ExitCode.AUTH
    if exc.status_code == 404:
        return ExitCode.PROTOCOL_OR_SCHEMA
    if exc.status_code == 429 or exc.status_code >= 500:
        return ExitCode.NETWORK_UNAVAILABLE
    return ExitCode.INTERNAL
