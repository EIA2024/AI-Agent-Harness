"""``personal-ai approvals`` — approval administration (CLI v2, T15).

The CLI is NOT a permission source of truth: it displays approvals and posts the
user's decision to the server, which owns policy + hash binding.
"""

from __future__ import annotations

import json
import sys
from typing import TextIO

import typer

from personal_ai_os.cli.bootstrap import build_client
from personal_ai_os.cli.commands.run_helper import run_admin
from personal_ai_os.cli.commands.table_output import emit, short_id

app = typer.Typer(help="Manage approvals")


async def _list_approvals(
    *, status: str, json_mode: bool, stdout: TextIO, stderr: TextIO
) -> None:
    async with build_client() as client:
        approvals = await client.list_approvals(status=status)
    if json_mode:
        stdout.write(
            json.dumps([a.__dict__ for a in approvals], ensure_ascii=False, indent=2) + "\n"
        )
        return
    emit(
        [
            {
                "id": short_id(a.id),
                "risk": f"R{a.risk_level}",
                "tool": a.tool_name,
                "summary": a.action_summary,
                "status": a.status,
            }
            for a in approvals
        ],
        columns=[("ID", "id"), ("RISK", "risk"), ("TOOL", "tool"), ("SUMMARY", "summary"), ("STATUS", "status")],
    )


@app.command("list")
def approvals_list(
    status: str = typer.Option("pending", help="Filter by status (pending/approved/rejected)"),
    json: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> None:
    """List approvals."""
    run_admin(lambda: _list_approvals(status=status, json_mode=json, stdout=sys.stdout, stderr=sys.stderr))


async def _resolve(approval_id: str, action: str, *, stdout: TextIO) -> None:
    async with build_client() as client:
        if action == "approve":
            result = await client.approve(approval_id)
        elif action == "reject":
            result = await client.reject(approval_id)
        else:
            raise ValueError(f"unknown action {action}")
    stdout.write(f"approval {short_id(result.id)} -> {result.status}\n")


@app.command("approve")
def approvals_approve(approval_id: str) -> None:
    """Approve a pending approval."""
    run_admin(lambda: _resolve(approval_id, "approve", stdout=sys.stdout))


@app.command("reject")
def approvals_reject(approval_id: str) -> None:
    """Reject a pending approval."""
    run_admin(lambda: _resolve(approval_id, "reject", stdout=sys.stdout))


async def _edit(approval_id: str, edited: str, *, stdout: TextIO) -> None:
    try:
        arguments = json.loads(edited)
    except json.JSONDecodeError:
        stdout.write("error: --json must be a valid JSON object\n")
        raise typer.Exit(code=2)
    if not isinstance(arguments, dict):
        stdout.write("error: --json must be a JSON object\n")
        raise typer.Exit(code=2)
    async with build_client() as client:
        result = await client.edit_approval(approval_id, arguments)
    stdout.write(f"approval {short_id(result.id)} -> {result.status} (args edited)\n")


@app.command("edit")
def approvals_edit(
    approval_id: str,
    json_arguments: str = typer.Option(..., "--json", help="Edited arguments as a JSON object"),
) -> None:
    """Edit a pending approval's arguments and approve (server re-hashes)."""
    run_admin(lambda: _edit(approval_id, json_arguments, stdout=sys.stdout))
