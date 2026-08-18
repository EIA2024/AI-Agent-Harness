from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_dev_script():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "dev.py"
    spec = importlib.util.spec_from_file_location("dev_script_under_test", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_dev_script_generates_and_reuses_local_auth(monkeypatch, tmp_path):
    dev = _load_dev_script()
    monkeypatch.setattr(dev, "ROOT", str(tmp_path))
    monkeypatch.setattr(dev, "ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.delenv("PERSONAL_AI_DEV_API_KEY", raising=False)
    monkeypatch.delenv("PERSONAL_AI_API_KEY", raising=False)
    monkeypatch.delenv("PERSONAL_AI_KEY_HASH_SECRET", raising=False)

    first = dev._env()
    second = dev._env()

    assert first["PERSONAL_AI_DEV_API_KEY"].startswith("paios-dev-")
    assert first["PERSONAL_AI_API_KEY"] == first["PERSONAL_AI_DEV_API_KEY"]
    assert first["PERSONAL_AI_KEY_HASH_SECRET"].startswith("paios-hash-")
    assert second["PERSONAL_AI_DEV_API_KEY"] == first["PERSONAL_AI_DEV_API_KEY"]
    assert second["PERSONAL_AI_KEY_HASH_SECRET"] == first["PERSONAL_AI_KEY_HASH_SECRET"]


def test_dev_script_respects_shell_env_without_writing_secret(monkeypatch, tmp_path):
    dev = _load_dev_script()
    monkeypatch.setattr(dev, "ROOT", str(tmp_path))
    monkeypatch.setattr(dev, "ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("PERSONAL_AI_DEV_API_KEY", "from-shell")
    monkeypatch.setenv("PERSONAL_AI_API_KEY", "from-shell")
    monkeypatch.setenv("PERSONAL_AI_KEY_HASH_SECRET", "hash-from-shell")

    env = dev._env()

    assert env["PERSONAL_AI_DEV_API_KEY"] == "from-shell"
    assert env["PERSONAL_AI_API_KEY"] == "from-shell"
    assert env["PERSONAL_AI_KEY_HASH_SECRET"] == "hash-from-shell"
    assert not (tmp_path / ".env").exists()


def test_dev_script_uses_configured_local_port():
    dev = _load_dev_script()

    assert dev._api_url({"PERSONAL_AI_PORT": "8001"}) == "http://127.0.0.1:8001"
