"""``personal-ai config`` — provider profile administration (CLI v2, T15).

Profiles are stored locally outside the repo (``ProviderConfigStore``); this
command mirrors the legacy ``config`` behavior with a cleaner surface.
"""

from __future__ import annotations

import json
import sys

import typer

from personal_ai_os.cli.commands.table_output import emit
from personal_ai_os.model_gateway import ProviderConfigStore

app = typer.Typer(help="Manage local LLM provider profiles (multi-API switching)")


@app.command("init")
def config_init() -> None:
    """Interactive first-run provider wizard (stores outside the repo)."""
    from personal_ai_os.cli.legacy import _wizard_init

    _wizard_init()


@app.command("list")
def config_list(json_mode: bool = typer.Option(False, "--json", help="JSON output")) -> None:
    """List saved provider profiles."""
    store = ProviderConfigStore()
    profiles = store.list_profiles()
    active = store.get_active_name()
    if json_mode:
        payload = [p.to_dict() | {"active": p.name == active} for p in profiles]
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return
    if not profiles:
        sys.stdout.write("no provider profiles configured — run `personal-ai config init`\n")
        return
    emit(
        [
            {
                "name": ("▶ " if p.name == active else "  ") + p.name,
                "format": p.format,
                "model": p.model or "-",
                "base_url": p.base_url or "-",
                "key": p.masked_key(),
            }
            for p in profiles
        ],
        columns=[("NAME", "name"), ("FORMAT", "format"), ("MODEL", "model"), ("BASE URL", "base_url"), ("KEY", "key")],
    )


@app.command("show")
def config_show(json_mode: bool = typer.Option(False, "--json", help="JSON output")) -> None:
    """Show the active provider profile."""
    store = ProviderConfigStore()
    profile = store.get_active()
    if profile is None:
        sys.stdout.write("no active provider — run `personal-ai config init`\n")
        return
    if json_mode:
        sys.stdout.write(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return
    sys.stdout.write(
        f"name      : {profile.name}\n"
        f"format    : {profile.format}\n"
        f"base_url  : {profile.base_url or '-'}\n"
        f"model     : {profile.model or '-'}\n"
        f"api_key   : {profile.masked_key()}\n"
        f"updated_at: {profile.updated_at}\n"
    )


@app.command("use")
def config_use(name: str) -> None:
    """Switch the active profile."""
    store = ProviderConfigStore()
    if store.set_active(name):
        sys.stdout.write(f"switched to {name!r}\n")
    else:
        sys.stdout.write(f"no profile named {name!r}\n")
        raise typer.Exit(code=1)


@app.command("edit")
def config_edit(name: str) -> None:
    """Interactively edit a profile (url/model/key)."""
    from personal_ai_os.cli.legacy import _prompt, _prompt_secret

    store = ProviderConfigStore()
    profile = store.get(name)
    if profile is None:
        sys.stdout.write(f"no profile named {name!r}\n")
        raise typer.Exit(code=1)
    fields: dict = {}
    new_url = _prompt(f"Base URL (current {profile.base_url or '-'})", "")
    if new_url:
        fields["base_url"] = new_url
    new_model = _prompt(f"Model (current {profile.model or '-'})", "")
    if new_model:
        fields["model"] = new_model
    new_key = _prompt_secret("New API Key (blank = unchanged)")
    if new_key:
        fields["api_key"] = new_key
    if fields:
        store.update(name, **fields)
        sys.stdout.write(f"updated {name!r}\n")
    else:
        sys.stdout.write("no changes\n")


@app.command("remove")
def config_remove(name: str) -> None:
    """Delete a profile."""
    store = ProviderConfigStore()
    if store.remove(name):
        sys.stdout.write(f"removed {name!r}\n")
    else:
        sys.stdout.write(f"no profile named {name!r}\n")
        raise typer.Exit(code=1)


@app.command("path")
def config_path() -> None:
    """Print the profiles file location."""
    store = ProviderConfigStore()
    sys.stdout.write(str(store.path) + "\n")
