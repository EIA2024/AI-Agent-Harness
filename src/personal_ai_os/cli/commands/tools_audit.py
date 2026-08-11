"""``personal-ai tools`` / ``personal-ai audit`` (CLI v2, T15)."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TextIO

import typer

from personal_ai_os.cli.bootstrap import build_client
from personal_ai_os.cli.commands.table_output import emit, short_id

tools_app = typer.Typer(help="List registered tools")
audit_app = typer.Typer(help="Inspect the audit log")


async def _list_tools(*, json_mode: bool, stdout: TextIO) -> None:
    async with build_client() as client:
        tools = await client.list_tools()
    if json_mode:
        stdout.write(json.dumps([t.__dict__ for t in tools], ensure_ascii=False, indent=2) + "\n")
        return
    emit(
        [
            {
                "name": t.name,
                "risk": f"R{t.risk_level}",
                "src": t.source,
                "desc": (t.description or "")[:50],
            }
            for t in tools
        ],
        columns=[("TOOL", "name"), ("RISK", "risk"), ("SOURCE", "src"), ("DESCRIPTION", "desc")],
    )


@tools_app.command("list")
def tools_list(json: bool = typer.Option(False, "--json", help="JSON output")) -> None:
    """List tools registered with the server."""
    asyncio.run(_list_tools(json_mode=json, stdout=sys.stdout))


async def _list_audit(limit: int, *, json_mode: bool, stdout: TextIO) -> None:
    async with build_client() as client:
        entries = await client.list_audit(limit=limit)
    if json_mode:
        stdout.write(json.dumps([e.__dict__ for e in entries], ensure_ascii=False, indent=2) + "\n")
        return
    emit(
        [
            {
                "id": short_id(e.id),
                "type": e.event_type,
                "resource": f"{e.resource_type}:{short_id(e.resource_id or '', 8)}",
                "at": e.created_at or "",
            }
            for e in entries
        ],
        columns=[("ID", "id"), ("EVENT", "type"), ("RESOURCE", "resource"), ("AT", "at")],
    )


@audit_app.command("list")
def audit_list(
    limit: int = typer.Option(50, help="Max entries"),
    json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """List recent audit log entries."""
    asyncio.run(_list_audit(limit, json_mode=json, stdout=sys.stdout))
