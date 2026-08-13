"""``personal-ai memories`` — memory administration (CLI v2, T15)."""

from __future__ import annotations

import json
import sys
from typing import TextIO

import typer

from personal_ai_os.cli.bootstrap import build_client
from personal_ai_os.cli.commands.run_helper import run_admin
from personal_ai_os.cli.commands.table_output import emit, short_id

app = typer.Typer(help="Manage memories")


def _rows(memories) -> list[dict]:
    return [
        {"id": short_id(m.id), "type": m.type, "scope": m.scope, "content": (m.content or "")[:60]}
        for m in memories
    ]


async def _list_memories(*, json_mode: bool, stdout: TextIO) -> None:
    async with build_client() as client:
        memories = await client.list_memories()
    if json_mode:
        stdout.write(json.dumps([m.__dict__ for m in memories], ensure_ascii=False, indent=2) + "\n")
        return
    emit(_rows(memories), columns=[("ID", "id"), ("TYPE", "type"), ("SCOPE", "scope"), ("CONTENT", "content")])


@app.command("list")
def memories_list(json: bool = typer.Option(False, "--json", help="JSON output")) -> None:
    """List memories."""
    run_admin(lambda: _list_memories(json_mode=json, stdout=sys.stdout))


async def _search_memories(query: str, limit: int, *, json_mode: bool, stdout: TextIO) -> None:
    async with build_client() as client:
        memories = await client.search_memories(query, limit=limit)
    if json_mode:
        stdout.write(json.dumps([m.__dict__ for m in memories], ensure_ascii=False, indent=2) + "\n")
        return
    emit(_rows(memories), columns=[("ID", "id"), ("TYPE", "type"), ("SCOPE", "scope"), ("CONTENT", "content")])


@app.command("search")
def memories_search(
    query: str,
    limit: int = typer.Option(10, help="Max results"),
    json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """Search memories."""
    run_admin(lambda: _search_memories(query, limit, json_mode=json, stdout=sys.stdout))


async def _show_memory(memory_id: str, *, stdout: TextIO) -> None:
    async with build_client() as client:
        memory = await client.get_memory(memory_id)
    stdout.write(json.dumps(memory.__dict__, ensure_ascii=False, indent=2) + "\n")


@app.command("show")
def memories_show(memory_id: str) -> None:
    """Show a memory's full detail (incl. provenance)."""
    run_admin(lambda: _show_memory(memory_id, stdout=sys.stdout))


async def _forget(memory_id: str, *, yes: bool, stdout: TextIO, stderr: TextIO) -> None:
    if not yes:
        stderr.write("refusing without confirmation — pass --yes to forget a memory\n")
        raise typer.Exit(code=2)
    async with build_client() as client:
        result = await client.forget_memory(memory_id)
    stdout.write(json.dumps(result, ensure_ascii=False) + "\n")


@app.command("forget")
def memories_forget(
    memory_id: str,
    yes: bool = typer.Option(False, "--yes", help="Confirm the destructive action"),
) -> None:
    """Forget (soft-delete) a memory. Requires --yes."""
    run_admin(lambda: _forget(memory_id, yes=yes, stdout=sys.stdout, stderr=sys.stderr))
