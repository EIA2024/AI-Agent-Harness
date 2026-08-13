from pathlib import Path


def _smoke_script() -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / "scripts" / "smoke.sh").read_text(encoding="utf-8")


def test_smoke_uses_portable_project_python_runner():
    script = _smoke_script()

    assert "command -v uv" in script
    assert "uv run python" in script
    assert ".venv/Scripts" not in script
    assert ".venv\\Scripts" not in script


def test_smoke_shares_generated_auth_with_server_and_requests():
    script = _smoke_script()

    assert "PERSONAL_AI_DEV_API_KEY" in script
    assert "PERSONAL_AI_API_KEY" in script
    assert "PERSONAL_AI_KEY_HASH_SECRET" in script
    assert 'X-API-Key: $SMOKE_API_KEY' in script


def test_smoke_waits_for_server_and_terminal_run_with_diagnostics():
    script = _smoke_script()

    assert "kill -0" in script
    assert "Server log:" in script
    assert "cleanup" in script
    assert "trap cleanup EXIT" in script
    assert "RUN_STATUS" in script
    for status in ("completed", "failed", "cancelled", "waiting_approval"):
        assert status in script
