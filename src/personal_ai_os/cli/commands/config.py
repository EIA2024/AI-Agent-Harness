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


def _public_profile(profile) -> dict:  # noqa: ANN001
    """Serialize provider metadata without ever copying the secret field."""
    return {
        "name": profile.name,
        "format": profile.format,
        "base_url": profile.base_url,
        "model": profile.model,
        "max_tokens": getattr(profile, "max_tokens", 0),
        "created_at": getattr(profile, "created_at", ""),
        "updated_at": getattr(profile, "updated_at", ""),
        "key_configured": bool(
            getattr(profile, "api_key", "") or getattr(profile, "secret_ref", "")
        ),
    }


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
        payload = [_public_profile(p) | {"active": p.name == active} for p in profiles]
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
        sys.stdout.write(json.dumps(_public_profile(profile), ensure_ascii=False, indent=2) + "\n")
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


@app.command("sources")
def config_sources() -> None:
    """Show the source precedence of each effective configuration value (T51)."""
    import os

    from personal_ai_os.cli.bootstrap import api_key, api_url

    store = ProviderConfigStore()
    active = store.get_active()
    url_src = "env PERSONAL_AI_API_URL" if os.environ.get("PERSONAL_AI_API_URL") else "default"
    key_src = "env PERSONAL_AI_API_KEY" if os.environ.get("PERSONAL_AI_API_KEY") else "default"
    sys.stdout.write(
        f"api_url : {api_url()}  < {url_src}\n"
        f"api_key : {'configured' if api_key() else 'missing'}  < {key_src}\n"
        f"profile : {active.name if active else '(none)'}  < ProviderConfigStore ({store.path})\n"
    )


@app.command("validate")
def config_validate() -> None:
    """Sanity-check all saved profiles (no secrets printed)."""
    store = ProviderConfigStore()
    problems: list[str] = []
    for profile in store.list_profiles():
        if profile.format not in ("openai", "anthropic"):
            problems.append(f"{profile.name}: unsupported format {profile.format!r}")
        if not profile.api_key:
            problems.append(f"{profile.name}: missing api_key")
        if not profile.base_url and profile.format == "openai":
            problems.append(f"{profile.name}: openai profile missing base_url")
    if problems:
        for problem in problems:
            sys.stdout.write(f"error: {problem}\n")
        raise typer.Exit(code=1)
    sys.stdout.write("all profiles valid\n")
