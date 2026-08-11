"""Global machine-level provider configuration and secrets."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from litletter.errors import ConfigurationError

_PROFILE_ID = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


@dataclass(frozen=True, slots=True)
class SecretConfig:
    """A literal secret or a reference to an environment variable."""

    value: str | None = field(default=None, repr=False)
    environment: str | None = None

    def resolve(self, *, environ: Mapping[str, str] | None = None) -> str:
        """Resolve the secret without exposing it in errors or representations."""
        if self.value is not None:
            return self.value
        variables = os.environ if environ is None else environ
        value = variables.get(self.environment or "")
        if value is None or not value.strip():
            raise ConfigurationError(
                f"environment variable {self.environment!r} is not set"
            )
        return value.strip()

    def resolve_optional(
        self, *, environ: Mapping[str, str] | None = None
    ) -> str | None:
        """Resolve a nonessential credential, returning None when its env is absent."""
        if self.value is not None:
            return self.value
        variables = os.environ if environ is None else environ
        value = variables.get(self.environment or "")
        return value.strip() if value and value.strip() else None

    def availability(self, *, environ: Mapping[str, str] | None = None) -> str:
        """Describe secret availability without returning or displaying its value."""
        if self.value is not None:
            return "available (stored in app config)"
        variables = os.environ if environ is None else environ
        available = bool((variables.get(self.environment or "") or "").strip())
        state = "set" if available else "not set"
        return f"{state} (environment {self.environment})"


@dataclass(frozen=True, slots=True)
class PubMedProviderConfig:
    """Credentials and caller identity for one PubMed profile."""

    id: str
    email: str
    api_key: SecretConfig | None


@dataclass(frozen=True, slots=True)
class DeepSeekProviderConfig:
    """Connection settings for one DeepSeek profile."""

    id: str
    api_key: SecretConfig
    base_url: str
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class PostmarkProviderConfig:
    """Credentials for one Postmark server."""

    id: str
    server_token: SecretConfig


@dataclass(frozen=True, slots=True)
class ResendProviderConfig:
    """Credentials for one Resend account."""

    id: str
    api_key: SecretConfig


MailerProviderConfig = PostmarkProviderConfig | ResendProviderConfig


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated global provider profiles shared by newsletters."""

    path: Path
    paper_sources: tuple[PubMedProviderConfig, ...]
    summarizers: tuple[DeepSeekProviderConfig, ...]
    mailers: tuple[MailerProviderConfig, ...]

    def pubmed(self, profile_id: str) -> PubMedProviderConfig:
        return _profile(self.paper_sources, profile_id, "paper source")

    def summarizer(self, profile_id: str) -> DeepSeekProviderConfig:
        return _profile(self.summarizers, profile_id, "summarizer")

    def mailer(self, profile_id: str) -> MailerProviderConfig:
        return _profile(self.mailers, profile_id, "mailer")

    def has_literal_secrets(self) -> bool:
        """Return whether this file itself contains any provider secret."""
        secrets = [
            *(profile.api_key for profile in self.paper_sources if profile.api_key),
            *(profile.api_key for profile in self.summarizers),
            *(
                profile.server_token
                if isinstance(profile, PostmarkProviderConfig)
                else profile.api_key
                for profile in self.mailers
            ),
        ]
        return any(secret.value is not None for secret in secrets)


def default_app_config_path(
    *, environ: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    """Return the global config location using explicit and XDG overrides."""
    variables = os.environ if environ is None else environ
    explicit = variables.get("LITLETTER_APP_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    xdg = variables.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else (home or Path.home()) / ".config"
    return base / "litletter" / "app.json"


def load_app_config(path: str | Path | None = None) -> AppConfig:
    """Load the global application config."""
    config_path = Path(path or default_app_config_path()).expanduser().resolve()
    try:
        with config_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise ConfigurationError(
            f"could not read app config: {config_path}; run 'litletter app-config init'"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"invalid app config JSON at line {exc.lineno}, column {exc.colno}: "
            f"{exc.msg}"
        ) from exc
    config = parse_app_config(payload, path=config_path)
    if (
        config.has_literal_secrets()
        and stat.S_IMODE(config_path.stat().st_mode) & 0o077
    ):
        raise ConfigurationError(
            f"app config contains literal secrets but is accessible by other users: "
            f"{config_path}; run 'chmod 600 {config_path}'"
        )
    return config


def parse_app_config(payload: Any, *, path: Path) -> AppConfig:
    """Validate an already-decoded global application config."""
    root = _object(payload, "app config")
    _only_keys(root, {"version", "providers"}, "app config")
    if root.get("version") != 1:
        raise ConfigurationError("app config.version must be 1")
    providers = _object(root.get("providers"), "providers")
    _only_keys(providers, {"paper_sources", "summarizers", "mailers"}, "providers")
    return AppConfig(
        path=path,
        paper_sources=_parse_pubmed_profiles(providers.get("paper_sources", {})),
        summarizers=_parse_deepseek_profiles(providers.get("summarizers", {})),
        mailers=_parse_mailer_profiles(providers.get("mailers", {})),
    )


def app_config_template() -> dict[str, Any]:
    """Return a safe-to-write starter configuration."""
    return {
        "version": 1,
        "providers": {
            "paper_sources": {
                "pubmed-default": {
                    "type": "pubmed",
                    "email": "you@example.com",
                    "api_key_env": "LITLETTER_NCBI_API_KEY",
                }
            },
            "summarizers": {
                "deepseek-default": {
                    "type": "deepseek",
                    "api_key_env": "LITLETTER_DEEPSEEK_API_KEY",
                    "base_url": "https://api.deepseek.com",
                }
            },
            "mailers": {
                "postmark-default": {
                    "type": "postmark",
                    "server_token_env": "LITLETTER_POSTMARK_TOKEN",
                },
                "resend-default": {
                    "type": "resend",
                    "api_key": "re_your_api_key",
                },
            },
        },
    }


def _parse_pubmed_profiles(value: Any) -> tuple[PubMedProviderConfig, ...]:
    raw = _object(value, "providers.paper_sources")
    profiles: list[PubMedProviderConfig] = []
    for profile_id, value in raw.items():
        location = f"providers.paper_sources.{profile_id}"
        _validate_profile_id(profile_id, location)
        profile = _object(value, location)
        _only_keys(profile, {"type", "email", "api_key", "api_key_env"}, location)
        if profile.get("type") != "pubmed":
            raise ConfigurationError(f"{location}.type must be 'pubmed'")
        profiles.append(
            PubMedProviderConfig(
                id=profile_id,
                email=_string(profile, "email", location),
                api_key=_optional_secret(profile, "api_key", location),
            )
        )
    return tuple(profiles)


def _parse_deepseek_profiles(value: Any) -> tuple[DeepSeekProviderConfig, ...]:
    raw = _object(value, "providers.summarizers")
    profiles: list[DeepSeekProviderConfig] = []
    for profile_id, value in raw.items():
        location = f"providers.summarizers.{profile_id}"
        _validate_profile_id(profile_id, location)
        profile = _object(value, location)
        _only_keys(
            profile,
            {"type", "api_key", "api_key_env", "base_url", "timeout_seconds"},
            location,
        )
        if profile.get("type") != "deepseek":
            raise ConfigurationError(f"{location}.type must be 'deepseek'")
        base_url = profile.get("base_url", "https://api.deepseek.com")
        if not isinstance(base_url, str) or not base_url.startswith("https://"):
            raise ConfigurationError(f"{location}.base_url must be an HTTPS URL")
        timeout = profile.get("timeout_seconds", 60)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ConfigurationError(f"{location}.timeout_seconds must be a number")
        if timeout <= 0:
            raise ConfigurationError(f"{location}.timeout_seconds must be > 0")
        profiles.append(
            DeepSeekProviderConfig(
                id=profile_id,
                api_key=_required_secret(profile, "api_key", location),
                base_url=base_url.rstrip("/"),
                timeout_seconds=float(timeout),
            )
        )
    return tuple(profiles)


def _parse_mailer_profiles(value: Any) -> tuple[MailerProviderConfig, ...]:
    raw = _object(value, "providers.mailers")
    profiles: list[MailerProviderConfig] = []
    for profile_id, value in raw.items():
        location = f"providers.mailers.{profile_id}"
        _validate_profile_id(profile_id, location)
        profile = _object(value, location)
        provider_type = profile.get("type")
        if provider_type == "postmark":
            _only_keys(
                profile,
                {"type", "server_token", "server_token_env"},
                location,
            )
            profiles.append(
                PostmarkProviderConfig(
                    id=profile_id,
                    server_token=_required_secret(profile, "server_token", location),
                )
            )
        elif provider_type == "resend":
            _only_keys(
                profile,
                {"type", "api_key", "api_key_env"},
                location,
            )
            profiles.append(
                ResendProviderConfig(
                    id=profile_id,
                    api_key=_required_secret(profile, "api_key", location),
                )
            )
        else:
            raise ConfigurationError(f"{location}.type must be 'postmark' or 'resend'")
    return tuple(profiles)


def _required_secret(value: dict[str, Any], key: str, location: str) -> SecretConfig:
    secret = _optional_secret(value, key, location)
    if secret is None:
        raise ConfigurationError(
            f"{location} requires exactly one of {key} or {key}_env"
        )
    return secret


def _optional_secret(
    value: dict[str, Any], key: str, location: str
) -> SecretConfig | None:
    literal = value.get(key)
    environment = value.get(f"{key}_env")
    if literal is None and environment is None:
        return None
    if literal is not None and environment is not None:
        raise ConfigurationError(f"{location} cannot contain both {key} and {key}_env")
    selected = literal if literal is not None else environment
    if not isinstance(selected, str) or not selected.strip():
        raise ConfigurationError(
            f"{location}.{key if literal is not None else f'{key}_env'} "
            "must be a non-empty string"
        )
    return SecretConfig(
        value=selected.strip() if literal is not None else None,
        environment=selected.strip() if environment is not None else None,
    )


def _profile(profiles: tuple[Any, ...], profile_id: str, kind: str) -> Any:
    for profile in profiles:
        if profile.id == profile_id:
            return profile
    raise ConfigurationError(f"unknown {kind} provider profile: {profile_id!r}")


def _validate_profile_id(profile_id: Any, location: str) -> None:
    if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
        raise ConfigurationError(
            f"{location} must use lowercase letters, numbers, _ or -"
        )


def _object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{location} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{location} has a non-string key")
    return value


def _only_keys(value: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigurationError(f"{location} has unknown keys: {', '.join(unknown)}")


def _string(value: dict[str, Any], key: str, location: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ConfigurationError(f"{location}.{key} must be a non-empty string")
    return result.strip()
