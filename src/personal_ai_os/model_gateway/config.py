"""Provider profile configuration store (local, outside the repo).

Lets the user keep **multiple** LLM provider profiles (OpenAI / Anthropic /
DeepSeek / Moonshot / …) and switch between them. Profile metadata is stored in
JSON outside the repository; API keys live only in the operating-system keyring.

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
from urllib.parse import urlparse

DEFAULT_CONFIG_DIR = Path.home() / ".personal_ai"
DEFAULT_CONFIG_FILE = "profiles.json"
REASONING_EFFORTS = ("auto", "low", "medium", "high")

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


def _endpoint_identity(base_url: str) -> tuple[str, int | None] | None:
    parsed = urlparse(base_url)
    if not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return parsed.hostname.lower(), port


def provider_identity(profile: ProviderProfile) -> str:
    """Derive a public provider identity without trusting the profile name."""
    if profile.format == "anthropic":
        return "anthropic"

    base_url = profile.base_url or KNOWN_PROVIDERS["openai"]["base_url"]
    endpoint = _endpoint_identity(base_url)
    for provider, preset in KNOWN_PROVIDERS.items():
        if preset["format"] != profile.format or not preset["base_url"]:
            continue
        if endpoint == _endpoint_identity(preset["base_url"]):
            return provider
    return "openai-compatible"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SecretVault:
    """OS-keyring storage for provider API keys."""

    _SERVICE = "personal-ai-os"

    @classmethod
    def set(cls, name: str, key: str) -> bool:
        try:
            import keyring  # type: ignore[import-not-found]

            keyring.set_password(cls._SERVICE, name, key)
            return True
        except Exception:  # noqa: BLE001 - keyring unavailable → file fallback
            return False

    @classmethod
    def get(cls, name: str) -> str | None:
        try:
            import keyring  # type: ignore[import-not-found]

            return keyring.get_password(cls._SERVICE, name)
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def delete(cls, name: str) -> bool:
        try:
            import keyring

            keyring.delete_password(cls._SERVICE, name)
            return True
        except Exception:  # noqa: BLE001
            return False


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
    reasoning_effort: str = "auto"
    max_tokens: int = 0  # 0 → provider default (4096 for reasoning models)
    created_at: str = ""
    updated_at: str = ""
    secret_ref: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now()
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.format not in ("openai", "anthropic"):
            raise ValueError(f"Unknown provider format: {self.format!r}")
        if self.reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"Unknown reasoning effort: {self.reasoning_effort!r}")

    def masked_key(self) -> str:
        """Return a display-safe form of the key (never the full value)."""
        if not self.api_key:
            return "(未设置)"
        if len(self.api_key) <= 8:
            return "*" * len(self.api_key)
        return self.api_key[:4] + "…" + self.api_key[-4:]

    def to_dict(self) -> dict:
        """Return display-safe metadata; never serialize ``api_key``."""
        data = self.to_storage_dict()
        data.pop("secret_ref", None)
        data["key_configured"] = bool(self.api_key or self.secret_ref)
        return data

    def to_storage_dict(self) -> dict:
        """Return JSON metadata containing only an opaque keyring reference."""
        data = asdict(self)
        data.pop("api_key", None)
        return data


def validate_provider_profile(profile: ProviderProfile) -> None:
    """Validate profile metadata shared by CLI and runtime reload paths."""
    if not profile.name.strip():
        raise ValueError("Provider profile name cannot be empty")
    if profile.format not in ("openai", "anthropic"):
        raise ValueError(f"Unknown provider format: {profile.format!r}")
    if profile.reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"Unknown reasoning effort: {profile.reasoning_effort!r}")
    _validate_base_url(profile.base_url, profile.name)


class ProviderConfigStore:
    """Persists profiles to a local JSON file outside the repo.

    Location: ``$PERSONAL_AI_CONFIG_DIR/profiles.json`` if set, else
    ``~/.personal_ai/profiles.json`` (on Windows ``C:\\Users\\<you>\\.personal_ai``).
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        vault: Any | None = None,
    ):
        self.vault = vault or SecretVault
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
        migrated = False
        for name, data in (raw.get("profiles") or {}).items():
            try:
                profile_data = dict(data)
                legacy_secret = str(profile_data.pop("api_key", "") or "")
                secret_ref = str(profile_data.get("secret_ref") or "")
                if legacy_secret:
                    if not self.vault.set(name, legacy_secret):
                        raise RuntimeError(
                            f"provider profile {name!r} contains a legacy plaintext key, "
                            "but the OS keyring is unavailable; the file was left unchanged"
                        )
                    secret_ref = name
                    profile_data["secret_ref"] = secret_ref
                    migrated = True
                key = self.vault.get(secret_ref) if secret_ref else None
                profile = ProviderProfile(api_key=key or "", **profile_data)
                validate_provider_profile(profile)
                profiles[name] = profile
            except (TypeError, ValueError):
                continue  # skip corrupt entries rather than fail the whole store
        active = raw.get("active")
        if migrated:
            self._save(profiles, active)
        return {"active": active, "profiles": profiles}

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
            "profiles": {
                name: p.to_storage_dict() for name, p in sorted(profiles.items())
            },
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

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
        validate_provider_profile(profile)
        if profile.api_key:
            if not self.vault.set(profile.name, profile.api_key):
                raise RuntimeError(
                    "OS keyring is unavailable; refusing to store an API key in plaintext"
                )
            profile.secret_ref = profile.name
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
        new_key = fields.pop("api_key", None)
        if new_key is not None:
            if new_key and not self.vault.set(name, str(new_key)):
                raise RuntimeError(
                    "OS keyring is unavailable; refusing to store an API key in plaintext"
                )
            profile.api_key = str(new_key)
            profile.secret_ref = name if new_key else ""
        for key, value in fields.items():
            if value is not None and hasattr(profile, key):
                setattr(profile, key, value)
        validate_provider_profile(profile)
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

    def clear_active(self) -> None:
        data = self.load()
        self._save(data["profiles"], None)

    def remove(self, name: str) -> bool:
        data = self.load()
        if name not in data["profiles"]:
            return False
        profile = data["profiles"][name]
        if profile.secret_ref:
            self.vault.delete(profile.secret_ref)
        del data["profiles"][name]
        if data["active"] == name:
            data["active"] = next(iter(data["profiles"]), None)
        self._save(data["profiles"], data["active"])
        return True
