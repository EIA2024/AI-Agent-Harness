"""``personal-ai sessions`` — session administration (CLI v2, T15)."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TextIO

import typer

from personal_ai_os.cli.bootstrap import build_client
from personal_ai_os.cli.commands.table_output import emit, short_id

app = typer.Typer(help="Manage sessions")


async def _list_sessions(
    *, limit: int, json_mode: bool, stdout: TextIO, stderr: TextIO
) -> None:
    async with build_client() as client:
        sessions = await client.list_sessions(limit=limit)
    if json_mode:
        stdout.write(
            json.dumps([s.__dict__ for s in sessions], ensure_ascii=False, indent=2) + "\n"
        )
        return
    emit(
        [
            {"id": short_id(s.id), "status": s.status, "title": s.title, "updated": s.last_active_at or ""}
            for s in sessions
        ],
        columns=[("ID", "id"), ("STATUS", "status"), ("TITLE", "title"), ("UPDATED", "updated")],
    )


@app.command("list")
def sessions_list(
    limit: int = typer.Option(50, help="Max sessions to list"),
    json: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> None:
    """List sessions (newest activity first)."""
    asyncio.run(_list_sessions(limit=limit, json_mode=json, stdout=sys.stdout, stderr=sys.stderr))


async def _show_session(session_id: str, *, stdout: TextIO) -> None:
    async with build_client() as client:
        detail = await client.get_session(session_id)
    stdout.write(json.dumps(detail, ensure_ascii=False, indent=2) + "\n")


@app.command("show")
def sessions_show(session_id: str) -> None:
    """Show a session's detail (incl. message history)."""
    asyncio.run(_show_session(session_id, stdout=sys.stdout))
