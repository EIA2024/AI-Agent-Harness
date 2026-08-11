"""Headless output renderers (CLI v2, T14).

Contract (ADR-003): in non-interactive mode stdout carries ONLY the requested
result protocol (answer text / JSON / JSONL), never spinners, ANSI, banners or
logs. Progress and diagnostics go to stderr. Exit codes are stable.
"""

from __future__ import annotations

import enum


class ExitCode(enum.IntEnum):
    """Stable CLI exit codes (plan §12.3)."""

    OK = 0
    USAGE_OR_CONFIG = 2
    AUTH = 3
    APPROVAL_REQUIRED = 4
    RUN_FAILED = 5
    CANCELLED = 6
    NETWORK_UNAVAILABLE = 7
    PROTOCOL_OR_SCHEMA = 8
    INTERNAL = 1


def exit_code_for_run_status(status: str) -> ExitCode:
    """Map a run terminal status to the headless exit code."""
    if status == "completed":
        return ExitCode.OK
    if status == "waiting_approval":
        return ExitCode.APPROVAL_REQUIRED
    if status == "failed":
        return ExitCode.RUN_FAILED
    if status == "cancelled":
        return ExitCode.CANCELLED
    return ExitCode.OK
