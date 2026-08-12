"""Provider profile configuration store (local, outside the repo).

Lets the user keep **multiple** LLM provider profiles (OpenAI / Anthropic /
DeepSeek / Moonshot / …) and switch between them. Secrets are stored in a JSON
file *outside* the git repository (default ``~/.personal_ai/profiles.json``) so
API keys never enter the repo.

Profile fields:

* ``format``   — ``"openai"`` (chat-completions compatible) or ``"anthropic"``
* ``base_url`` — API root; ``/chat/completions`` is appended for openai format
* ``api_key``  — the secret
* ``model``    — default model id for this profile

The store is a thin, dependency-free JSON persistence layer used by both the
CLI (``personal-ai config …``) and the API service wiring.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_DIR = Path.home() / ".personal_ai"
DEFAULT_CONFIG_FILE = "profiles.json"

#: Common OpenAI-compatible providers → sensible defaults for the wizard.
KNOWN_PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {"format": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "anthropic": {"format": "anthropic", "base_url": "", "model": "claude-sonnet-5"},
    "deepseek": {"format": "openai", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "moonshot": {"format": "openai", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "zhipu": {"format": "openai", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "qwen": {"format": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "ollama": {"format": "openai", "base_url": "http://localhost:11434/v1", "model": "llama3.1"},
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_loopback(host: str) -> bool:
    import ipaddress

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() in ("localhost", "127.0.0.1", "::1")


def _validate_base_url(base_url: str, name: str) -> None:
    """Reject a provider base_url that would leak the API key over plaintext.

    P1-018: custom endpoints must be https (or loopback http for local
    models). Sending an API key to an arbitrary http:// host is refused.
    """
    if not base_url:
        return
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"profile {name!r}: base_url must be http(s), got {parsed.scheme!r}"
        )
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname or ""):
        raise ValueError(
            f"profile {name!r}: http base_url is only allowed for loopback "
            f"(localhost); use https for {parsed.hostname!r} (P1-018)"
        )


@dataclass
class ProviderProfile:
    """A single LLM provider profile."""

    name: str
    format: str  # "openai" | "anthropic"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    max_tokens: int = 0  # 0 → provider default (4096 for reasoning models)
    created_at: str = ""
    updated_at: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now()
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.format not in ("openai", "anthropic"):
            raise ValueError(f"Unknown provider format: {self.format!r}")

    def masked_key(self) -> str:
        """Return a display-safe form of the key (never the full value)."""
        if not self.api_key:
            return "(未设置)"
        if len(self.api_key) <= 8:
            return "*" * len(self.api_key)
        return self.api_key[:4] + "…" + self.api_key[-4:]

    def to_dict(self) -> dict:
        return asdict(self)


class ProviderConfigStore:
    """Persists profiles to a local JSON file outside the repo.

    Location: ``$PERSONAL_AI_CONFIG_DIR/profiles.json`` if set, else
    ``~/.personal_ai/profiles.json`` (on Windows ``C:\\Users\\<you>\\.personal_ai``).
    """

    def __init__(self, path: Path | str | None = None):
        if path is not None:
            self.path = Path(path)
            return
        config_dir = os.environ.get("PERSONAL_AI_CONFIG_DIR")
        self.path = (Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR) / DEFAULT_CONFIG_FILE

    # -- read --------------------------------------------------------------

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict[str, Any]:
        """Return ``{"active": str|None, "profiles": {name: ProviderProfile}}``."""
        if not self.path.exists():
            return {"active": None, "profiles": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"active": None, "profiles": {}}
        profiles = {}
        for name, data in (raw.get("profiles") or {}).items():
            try:
                profiles[name] = ProviderProfile(**data)
            except (TypeError, ValueError):
                continue  # skip corrupt entries rather than fail the whole store
        return {"active": raw.get("active"), "profiles": profiles}

    # -- write -------------------------------------------------------------

    def _save(self, profiles: dict[str, ProviderProfile], active: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # keep the file private (best-effort; on Windows this is advisory)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        payload = {
            "active": active,
            "profiles": {name: p.to_dict() for name, p in sorted(profiles.items())},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # -- operations --------------------------------------------------------

    def list_profiles(self) -> list[ProviderProfile]:
        return sorted(self.load()["profiles"].values(), key=lambda p: p.name)

    def get(self, name: str) -> ProviderProfile | None:
        return self.load()["profiles"].get(name)

    def get_active(self) -> ProviderProfile | None:
        data = self.load()
        active = data["active"]
        if not active:
            return None
        return data["profiles"].get(active)

    def get_active_name(self) -> str | None:
        return self.load()["active"]

    def add(self, profile: ProviderProfile, *, activate: bool = True) -> None:
        _validate_base_url(profile.base_url, profile.name)
        data = self.load()
        data["profiles"][profile.name] = profile
        if activate or data["active"] is None:
            data["active"] = profile.name
        self._save(data["profiles"], data["active"])

    def update(self, name: str, **fields: Any) -> ProviderProfile | None:
        data = self.load()
        profile = data["profiles"].get(name)
        if profile is None:
            return None
        for key, value in fields.items():
            if value is not None and hasattr(profile, key):
                setattr(profile, key, value)
        if "base_url" in fields:
            _validate_base_url(profile.base_url, profile.name)
        profile.updated_at = _now()
        self._save(data["profiles"], data["active"])
        return profile

    def set_active(self, name: str) -> bool:
        data = self.load()
        if name not in data["profiles"]:
            return False
        data["active"] = name
        self._save(data["profiles"], name)
        return True

    def remove(self, name: str) -> bool:
        data = self.load()
        if name not in data["profiles"]:
            return False
        del data["profiles"][name]
        if data["active"] == name:
            data["active"] = next(iter(data["profiles"]), None)
        self._save(data["profiles"], data["active"])
        return True
