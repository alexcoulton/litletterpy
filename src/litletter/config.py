"""Load and validate versioned Litletter JSON configuration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from litletter.errors import ConfigurationError
from litletter.models import PaperSource
from litletter.query import parse_query

_CATEGORY_ID = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


@dataclass(frozen=True, slots=True)
class NewsletterConfig:
    """Presentation and addressing for generated editions."""

    title: str
    from_address: str
    to: tuple[str, ...]
    timezone: str
    abstract_max_characters: int


@dataclass(frozen=True, slots=True)
class PubMedConfig:
    """PubMed access settings."""

    enabled: bool
    email: str
    api_key_env: str | None


@dataclass(frozen=True, slots=True)
class BioRxivConfig:
    """bioRxiv access settings."""

    enabled: bool


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    """Date-window behavior for recurring discovery."""

    initial_lookback_days: int
    overlap_days: int


@dataclass(frozen=True, slots=True)
class CategoryConfig:
    """One named newsletter section and its paper query."""

    id: str
    name: str
    query: str
    sources: tuple[PaperSource, ...]


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    """Postmark delivery settings."""

    provider: str
    token_env: str
    message_stream: str


@dataclass(frozen=True, slots=True)
class LitletterConfig:
    """Fully validated application configuration."""

    path: Path
    database: Path
    newsletter: NewsletterConfig
    pubmed: PubMedConfig
    biorxiv: BioRxivConfig
    discovery: DiscoveryConfig
    categories: tuple[CategoryConfig, ...]
    delivery: DeliveryConfig


def load_config(path: str | Path) -> LitletterConfig:
    """Load a JSON configuration file and return its validated representation."""
    config_path = Path(path).expanduser().resolve()
    try:
        with config_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise ConfigurationError(f"could not read config: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    return parse_config(payload, path=config_path)


def parse_config(payload: Any, *, path: Path) -> LitletterConfig:
    """Validate an already-decoded JSON configuration."""
    root = _object(payload, "config")
    _only_keys(
        root,
        {
            "version",
            "database",
            "newsletter",
            "sources",
            "discovery",
            "categories",
            "delivery",
        },
        "config",
    )
    if root.get("version") != 1:
        raise ConfigurationError("config.version must be 1")

    raw_database = _string(root, "database", "config")
    database = Path(raw_database).expanduser()
    if not database.is_absolute():
        database = path.parent / database
    database = database.resolve()

    newsletter = _parse_newsletter(root.get("newsletter"))
    pubmed, biorxiv = _parse_sources(root.get("sources"))
    discovery = _parse_discovery(root.get("discovery"))
    categories = _parse_categories(root.get("categories"), pubmed, biorxiv)
    delivery = _parse_delivery(root.get("delivery"))
    return LitletterConfig(
        path=path,
        database=database,
        newsletter=newsletter,
        pubmed=pubmed,
        biorxiv=biorxiv,
        discovery=discovery,
        categories=categories,
        delivery=delivery,
    )


def _parse_newsletter(value: Any) -> NewsletterConfig:
    raw = _object(value, "newsletter")
    _only_keys(
        raw,
        {"title", "from", "to", "timezone", "abstract_max_characters"},
        "newsletter",
    )
    recipients = _string_list(raw.get("to"), "newsletter.to")
    timezone = _string(raw, "timezone", "newsletter")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigurationError(
            f"newsletter.timezone is unknown: {timezone!r}"
        ) from exc
    abstract_max = _integer(
        raw.get("abstract_max_characters", 800),
        "newsletter.abstract_max_characters",
        minimum=0,
    )
    return NewsletterConfig(
        title=_string(raw, "title", "newsletter"),
        from_address=_string(raw, "from", "newsletter"),
        to=recipients,
        timezone=timezone,
        abstract_max_characters=abstract_max,
    )


def _parse_sources(value: Any) -> tuple[PubMedConfig, BioRxivConfig]:
    raw = _object(value, "sources")
    _only_keys(raw, {"pubmed", "biorxiv"}, "sources")

    raw_pubmed = _object(raw.get("pubmed", {}), "sources.pubmed")
    _only_keys(raw_pubmed, {"enabled", "email", "api_key_env"}, "sources.pubmed")
    pubmed_enabled = _boolean(raw_pubmed.get("enabled", True), "sources.pubmed.enabled")
    email = raw_pubmed.get("email", "")
    if not isinstance(email, str) or (pubmed_enabled and not email.strip()):
        raise ConfigurationError("sources.pubmed.email must be a non-empty string")
    api_key_env = raw_pubmed.get("api_key_env")
    if api_key_env is not None and (
        not isinstance(api_key_env, str) or not api_key_env.strip()
    ):
        raise ConfigurationError(
            "sources.pubmed.api_key_env must be a non-empty string or null"
        )

    raw_biorxiv = _object(raw.get("biorxiv", {}), "sources.biorxiv")
    _only_keys(raw_biorxiv, {"enabled"}, "sources.biorxiv")
    biorxiv_enabled = _boolean(
        raw_biorxiv.get("enabled", True), "sources.biorxiv.enabled"
    )
    return (
        PubMedConfig(pubmed_enabled, email.strip(), api_key_env),
        BioRxivConfig(biorxiv_enabled),
    )


def _parse_discovery(value: Any) -> DiscoveryConfig:
    raw = _object(value, "discovery")
    _only_keys(raw, {"initial_lookback_days", "overlap_days"}, "discovery")
    initial = _integer(
        raw.get("initial_lookback_days", 30),
        "discovery.initial_lookback_days",
        minimum=0,
    )
    overlap = _integer(
        raw.get("overlap_days", 2),
        "discovery.overlap_days",
        minimum=0,
    )
    return DiscoveryConfig(initial, overlap)


def _parse_categories(
    value: Any,
    pubmed: PubMedConfig,
    biorxiv: BioRxivConfig,
) -> tuple[CategoryConfig, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigurationError("categories must be a non-empty array")
    categories: list[CategoryConfig] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        location = f"categories[{index}]"
        raw = _object(item, location)
        _only_keys(raw, {"id", "name", "query", "sources"}, location)
        category_id = _string(raw, "id", location)
        if not _CATEGORY_ID.fullmatch(category_id):
            raise ConfigurationError(
                f"{location}.id must contain lowercase letters, numbers, _ or -"
            )
        if category_id in seen:
            raise ConfigurationError(f"duplicate category id: {category_id!r}")
        seen.add(category_id)
        query = _string(raw, "query", location)
        try:
            parse_query(query)
        except ValueError as exc:
            raise ConfigurationError(f"{location}.query is invalid: {exc}") from exc

        raw_sources = _string_list(raw.get("sources"), f"{location}.sources")
        try:
            sources = tuple(PaperSource(source) for source in raw_sources)
        except ValueError as exc:
            raise ConfigurationError(
                f"{location}.sources contains an unsupported source"
            ) from exc
        if len(set(sources)) != len(sources):
            raise ConfigurationError(f"{location}.sources contains duplicates")
        if PaperSource.PUBMED in sources and not pubmed.enabled:
            raise ConfigurationError(f"{location} uses disabled source 'pubmed'")
        if PaperSource.BIORXIV in sources and not biorxiv.enabled:
            raise ConfigurationError(f"{location} uses disabled source 'biorxiv'")
        categories.append(
            CategoryConfig(
                id=category_id,
                name=_string(raw, "name", location),
                query=query,
                sources=sources,
            )
        )
    return tuple(categories)


def _parse_delivery(value: Any) -> DeliveryConfig:
    raw = _object(value, "delivery")
    _only_keys(raw, {"provider", "token_env", "message_stream"}, "delivery")
    provider = _string(raw, "provider", "delivery")
    if provider != "postmark":
        raise ConfigurationError("delivery.provider must be 'postmark'")
    return DeliveryConfig(
        provider=provider,
        token_env=_string(raw, "token_env", "delivery"),
        message_stream=_string(raw, "message_stream", "delivery"),
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


def _string_list(value: Any, location: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigurationError(f"{location} must be a non-empty array")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ConfigurationError(f"{location} must contain non-empty strings")
    result = tuple(item.strip() for item in value)
    if len(set(result)) != len(result):
        raise ConfigurationError(f"{location} contains duplicates")
    return result


def _integer(value: Any, location: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigurationError(f"{location} must be an integer >= {minimum}")
    return value


def _boolean(value: Any, location: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{location} must be a boolean")
    return value
