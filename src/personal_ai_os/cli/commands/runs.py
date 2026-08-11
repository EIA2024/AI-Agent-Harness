"""``personal-ai runs`` — run administration (CLI v2, T15).

Phase 4 (T40) adds a real ``GET /v1/runs`` list endpoint; until then ``list``
uses the session ``active_run_id`` workaround (same data the legacy CLI used).
"""

from __future__ import annotations

import json
import sys
from typing import TextIO

import typer

from personal_ai_os.cli.bootstrap import build_client
from personal_ai_os.cli.commands.run_helper import run_admin
from personal_ai_os.cli.commands.table_output import emit, short_id

app = typer.Typer(help="Manage runs")


async def _list_runs(*, limit: int, json_mode: bool, stdout: TextIO, stderr: TextIO) -> None:
    async with build_client() as client:
        runs = await client.list_runs(limit=limit)
    rows = [
        {
            "id": short_id(r.id),
            "status": r.status,
            "session": short_id(r.session_id or "", 8),
        }
        for r in runs
    ]
    if json_mode:
        stdout.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        return
    emit(rows, columns=[("ID", "id"), ("STATUS", "status"), ("SESSION", "session")])


@app.command("list")
def runs_list(
    limit: int = typer.Option(10, help="Max runs to list"),
    json: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> None:
    """List recent runs (via session active runs until T40 lands)."""
    run_admin(lambda: _list_runs(limit=limit, json_mode=json, stdout=sys.stdout, stderr=sys.stderr))


async def _show_run(run_id: str, *, stdout: TextIO) -> None:
    async with build_client() as client:
        run = await client.get_run(run_id)
    stdout.write(
        json.dumps(
            run.__dict__,
            ensure_ascii=False,
            indent=2,
            default=lambda o: o.__dict__ if hasattr(o, "__dict__") else str(o),
        )
        + "\n"
    )


@app.command("show")
def runs_show(run_id: str) -> None:
    """Show a run's full detail."""
    run_admin(lambda: _show_run(run_id, stdout=sys.stdout))


@app.command("get")
def runs_get(run_id: str) -> None:
    """Legacy alias of ``runs show``."""
    run_admin(lambda: _show_run(run_id, stdout=sys.stdout))


async def _cancel_run(run_id: str, *, stdout: TextIO) -> None:
    async with build_client() as client:
        run = await client.cancel_run(run_id)
    stdout.write(f"run {run.id} cancelled (status={run.status})\n")


@app.command("cancel")
def runs_cancel(run_id: str) -> None:
    """Cancel a running/pending run."""
    run_admin(lambda: _cancel_run(run_id, stdout=sys.stdout))


async def _resume_run(
    run_id: str, *, approval_id: str | None, decision: str | None, stdout: TextIO
) -> None:
    async with build_client() as client:
        run = await client.resume_run(run_id, approval_id=approval_id, decision=decision)
    stdout.write(f"run {run.id} resumed (status={run.status})\n")


@app.command("resume")
def runs_resume(
    run_id: str,
    approval_id: str | None = typer.Option(None, help="Approval id to resolve"),
    decision: str | None = typer.Option(None, help="approved | rejected | approved_with_edits"),
) -> None:
    """Resume a waiting run."""
    run_admin(lambda: _resume_run(run_id, approval_id=approval_id, decision=decision, stdout=sys.stdout))
