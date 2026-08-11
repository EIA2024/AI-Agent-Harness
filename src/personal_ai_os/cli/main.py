"""Personal AI OS — CLI v2 entry point (Typer root).

Command tree (see ``personal-ai --help``)::

    personal-ai                      interactive chat (TTY) / help (non-TTY)
    personal-ai exec "..." [-o text|json|stream-json] [--session ID]
    personal-ai sessions|runs|approvals|memories|tools|audit|config|doctor
    personal-ai chat|send|approve   legacy compatibility aliases

The root only parses arguments and dispatches. Network / rendering live in the
``commands`` and ``controllers`` packages; ``legacy.py`` keeps the pre-v2
commands available as compatibility aliases.
"""

from __future__ import annotations

import asyncio
import sys

import typer

from personal_ai_os.cli.bootstrap import build_client
from personal_ai_os.cli.commands import approvals, config, memories, runs, sessions, tools_audit
from personal_ai_os.cli.commands.doctor import doctor_command
from personal_ai_os.cli.commands.exec import run_exec

app = typer.Typer(
    name="personal-ai",
    help=(
        "Personal AI OS — a terminal control plane for your personal agent runtime. "
        "Run `personal-ai` for the interactive UI, `personal-ai exec` for one-shot "
        "headless runs, and `personal-ai sessions|runs|approvals|...` to inspect state."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)


@app.callback()
def _root(
    ctx: typer.Context,
    continue_session: bool = typer.Option(
        False, "--continue", help="Resume the most recent session"
    ),
    session: str | None = typer.Option(
        None, "--session", help="Resume a specific session id"
    ),
    pick: bool = typer.Option(
        False, "--pick", help="Choose a session to resume interactively"
    ),
) -> None:
    """With no subcommand, enter the interactive UI (TTY only)."""
    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            session_id = _resolve_session(session, continue_session=continue_session, pick=pick)
            from personal_ai_os.cli.tui import run_app

            run_app(session_id=session_id)
        else:
            typer.echo(ctx.get_help())
            raise typer.Exit(code=0)


def _resolve_session(
    explicit: str | None, *, continue_session: bool, pick: bool
) -> str | None:
    """Resolve the session to resume: explicit id > --continue > --pick."""
    if explicit:
        return explicit
    if not (continue_session or pick):
        return None
    import asyncio

    from personal_ai_os.cli.bootstrap import build_client

    async def _list() -> list[dict]:
        async with build_client() as client:
            sessions = await client.list_sessions(status="active", limit=20)
        return [
            {
                "id": s.id,
                "title": s.title,
                "status": s.status,
                "updated": s.last_active_at or "",
            }
            for s in sessions
        ]

    sessions = asyncio.run(_list())
    if not sessions:
        typer.echo("no sessions to resume", err=True)
        return None
    if continue_session:
        return sessions[0]["id"]
    # interactive picker (TTY)
    typer.echo("Sessions:")
    for i, s in enumerate(sessions, 1):
        title = (s["title"] or "")[:40]
        typer.echo(f"  {i}. {s['id'][:8]}  {title}")
    choice = typer.prompt("Pick a session (number)", type=int, default=1)
    if 1 <= choice <= len(sessions):
        return sessions[choice - 1]["id"]
    typer.echo("invalid choice", err=True)
    return None


# ---------------------------------------------------------------------------
# exec — headless single run
# ---------------------------------------------------------------------------


def _run_exec(
    text: str,
    *,
    session: str | None,
    output: str,
) -> None:
    async def _main() -> int:
        async with build_client() as client:
            return await run_exec(
                client,
                prompt=text,
                session_id=session,
                output_format=output,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )

    code = asyncio.run(_main())
    raise typer.Exit(code=code)


def _resolve_prompt(prompt: str | None) -> str:
    if prompt is None or prompt == "-":
        return sys.stdin.read()
    return prompt


@app.command("exec", help="Run a single prompt headless.")
def exec_command(
    prompt: str = typer.Argument(
        None,
        help="Prompt text, or `-` to read stdin. Omit to read stdin when piped.",
    ),
    session: str | None = typer.Option(None, "--session", help="Existing session id"),
    output: str = typer.Option(
        "text", "-o", "--output", help="Output format: text | json | stream-json"
    ),
) -> None:
    text = _resolve_prompt(prompt)
    if not text.strip():
        typer.echo("error: empty prompt", err=True)
        raise typer.Exit(code=2)
    if output not in ("text", "json", "stream-json"):
        typer.echo(f"error: unsupported output format {output!r}", err=True)
        raise typer.Exit(code=2)
    _run_exec(text, session=session, output=output)


# ---------------------------------------------------------------------------
# admin sub-commands
# ---------------------------------------------------------------------------

app.add_typer(sessions.app, name="sessions")
app.add_typer(runs.app, name="runs")
app.add_typer(approvals.app, name="approvals")
app.add_typer(memories.app, name="memories")
app.add_typer(tools_audit.tools_app, name="tools")
app.add_typer(tools_audit.audit_app, name="audit")
app.add_typer(config.app, name="config")


@app.command("doctor", help="Diagnose CLI / API / provider setup.")
def _doctor() -> None:
    doctor_command()


# ---------------------------------------------------------------------------
# legacy compatibility aliases
# ---------------------------------------------------------------------------


@app.command("chat", help="Interactive chat (legacy alias of the default UI).", hidden=True)
def chat_legacy(
    session: str | None = typer.Option(None, "--session", help="Existing session id"),
    no_thinking: bool = typer.Option(False, "--no-thinking", help="Hide chain of thought"),
) -> None:
    _interactive_with(session=session, no_thinking=no_thinking)


def _interactive_with(*, session: str | None, no_thinking: bool) -> None:
    import argparse

    from personal_ai_os.cli.legacy import cmd_chat

    cmd_chat(argparse.Namespace(session=session, no_thinking=no_thinking))


@app.command("send", help="Send a single message (deprecated; use `exec`).", hidden=True)
def send_legacy(
    text: str = typer.Argument(..., help="Message text"),
    session: str | None = typer.Option(None, "--session", help="Existing session id"),
) -> None:
    typer.echo("Warning: `personal-ai send` is deprecated; use `personal-ai exec`.", err=True)
    _run_exec(text, session=session, output="text")


@app.command("approve", help="Legacy approval command (deprecated; use `approvals`).", hidden=True)
def approve_legacy(
    approval_id: str | None = typer.Argument(None, help="Approval id to approve"),
    list_: bool = typer.Option(False, "--list", help="List pending approvals"),
) -> None:
    typer.echo(
        "Warning: `personal-ai approve` is deprecated; use `personal-ai approvals`.", err=True
    )
    if list_:
        from personal_ai_os.cli.commands.approvals import approvals_list

        approvals_list()
        return
    if not approval_id:
        typer.echo("Provide an approval id or use --list.", err=True)
        raise typer.Exit(code=2)
    from personal_ai_os.cli.commands.approvals import approvals_approve

    approvals_approve(approval_id)


def _ensure_utf8() -> None:
    """Force UTF-8 text streams (Windows uses the locale codec otherwise,
    which cannot encode Rich's box-drawing help output)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover - no reconfigure
            pass


def main() -> int:
    """Console-script entry point."""
    _ensure_utf8()
    app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
