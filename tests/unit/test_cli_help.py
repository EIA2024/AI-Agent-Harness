"""CLI smoke tests: help output must exit 0."""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([ROOT, os.path.join(ROOT, "src")])
    return env


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "apps.cli.main", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_env(),
        timeout=60,
    )


def test_cli_help_exit_zero():
    result = _run_cli("--help")
    assert result.returncode == 0, result.stderr
    assert "usage" in (result.stdout + result.stderr).lower()


def test_cli_subcommand_helps_exit_zero():
    for args in (
        ["chat", "--help"],
        ["send", "--help"],
        ["sessions", "--help"],
        ["sessions", "list", "--help"],
        ["runs", "--help"],
        ["runs", "list", "--help"],
        ["memories", "--help"],
        ["memories", "search", "--help"],
        ["approve", "--help"],
    ):
        result = _run_cli(*args)
        assert result.returncode == 0, f"{args} -> {result.stderr}"
        assert "usage" in (result.stdout + result.stderr).lower()


def test_cli_no_command_prints_help_and_exits_zero():
    result = _run_cli()
    assert result.returncode == 0
    assert "usage" in (result.stdout + result.stderr).lower()
