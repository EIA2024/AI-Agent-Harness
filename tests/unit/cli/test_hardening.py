"""T50/T51/T53 — doctor diagnostics, config sources, notifications."""

from __future__ import annotations

import io

from personal_ai_os.cli.api.errors import TransportError
from personal_ai_os.cli.commands.doctor import diagnose
from personal_ai_os.cli.notify import approval_required, notifications_enabled


class _UnreachableClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def healthz(self):
        raise TransportError("connection refused")

    async def list_tools(self):
        raise TransportError("connection refused")


def test_doctor_never_leaks_secret(monkeypatch, tmp_path, capsys):
    import personal_ai_os.cli.commands.doctor as doctor_mod

    class FakeStore:
        path = tmp_path / "profiles.json"

        def list_profiles(self):
            from types import SimpleNamespace

            return [SimpleNamespace(name="main", format="openai", api_key="sk-SUPER-SECRET")]

        def get_active_name(self):
            return "main"

    monkeypatch.setattr(doctor_mod, "ProviderConfigStore", FakeStore)
    monkeypatch.setattr(doctor_mod, "build_client", lambda *a, **k: _UnreachableClient())
    monkeypatch.setattr(doctor_mod, "api_key", lambda: "sk-SUPER-SECRET")

    out = io.StringIO()
    code = doctor_mod.asyncio.run(diagnose(out))
    rendered = out.getvalue()
    assert "sk-SUPER-SECRET" not in rendered
    assert "provider" in rendered
    assert code == 1  # unreachable API → not all OK


def test_doctor_json_mode(monkeypatch, tmp_path):
    import json

    import personal_ai_os.cli.commands.doctor as doctor_mod

    class FakeStore:
        path = tmp_path / "profiles.json"

        def list_profiles(self):
            return []

        def get_active_name(self):
            return None

    monkeypatch.setattr(doctor_mod, "ProviderConfigStore", FakeStore)
    monkeypatch.setattr(doctor_mod, "build_client", lambda *a, **k: _UnreachableClient())
    monkeypatch.setattr(doctor_mod, "api_key", lambda: "k")

    out = io.StringIO()
    doctor_mod.asyncio.run(diagnose(out, json_mode=True))
    payload = json.loads(out.getvalue())
    assert isinstance(payload, list)
    assert all({"name", "status", "message"} <= set(c) for c in payload)


def test_notifications_opt_in(monkeypatch, capsys):
    monkeypatch.delenv("PERSONAL_AI_NOTIFY", raising=False)
    assert notifications_enabled() is False
    approval_required()  # no-op, nothing written
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("PERSONAL_AI_NOTIFY", "1")
    assert notifications_enabled() is True
    approval_required()
    assert "\a" in capsys.readouterr().err


def test_config_sources_shows_precedence(monkeypatch, tmp_path, capsys):
    from personal_ai_os.cli.commands.config import config_sources

    monkeypatch.delenv("PERSONAL_AI_API_URL", raising=False)
    monkeypatch.delenv("PERSONAL_AI_API_KEY", raising=False)
    config_sources()
    out = capsys.readouterr().out
    assert "api_url" in out and "default" in out and "profile" in out
