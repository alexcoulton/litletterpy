"""User-owned named author collections used by Litletter queries."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from functools import cache
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any

from litletter.errors import AuthorCatalogError, UnknownAuthorGroupError

_ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]\Z", re.IGNORECASE)
_BUILTIN_CATALOGS = {"cancer_researchers": "cancer_researchers.json"}


@dataclass(frozen=True, slots=True)
class AuthorIdentity:
    """A watched researcher with optional disambiguating metadata."""

    name: str | None
    orcid: str | None = None
    aliases: tuple[str, ...] = ()
    institution: str | None = None
    match_initials: bool = False

    def __post_init__(self) -> None:
        if self.name is None and self.orcid is None:
            raise ValueError("an author identity requires a name or ORCID")

    @property
    def query_text(self) -> str:
        """Return the safest text for upstream author candidate selection."""
        return self.name or self.orcid or ""

    def fingerprint(self) -> tuple[object, ...]:
        """Return fields that affect paper matching."""
        return (self.name, self.orcid, self.aliases, self.match_initials)


@dataclass(frozen=True, slots=True)
class AuthorGroup:
    """A named collection of author names or ORCIDs."""

    name: str
    description: str
    authors: tuple[AuthorIdentity, ...]


@dataclass(frozen=True, slots=True)
class AuthorCatalog:
    """An immutable catalog loaded from a user's author-groups file."""

    path: Path
    groups: Mapping[str, AuthorGroup]
    aliases: Mapping[str, str]
    as_of: str | None = None
    methodology: str | None = None
    source_urls: tuple[str, ...] = ()

    def get(self, name: str) -> AuthorGroup:
        """Return a group by case-insensitive name or alias."""
        key = normalize_group_name(name)
        key = self.aliases.get(key, key)
        try:
            return self.groups[key]
        except KeyError as exc:
            raise UnknownAuthorGroupError(name, self.names()) from exc

    def names(self) -> tuple[str, ...]:
        """Return canonical group names in alphabetical order."""
        return tuple(sorted(self.groups))


def load_author_catalog(path: str | Path) -> AuthorCatalog:
    """Load and validate an author-groups JSON file."""
    catalog_path = Path(path).expanduser().resolve()
    try:
        with catalog_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise AuthorCatalogError(
            f"could not read author groups: {catalog_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise AuthorCatalogError(
            "invalid author-groups JSON at "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    return _parse_catalog(payload, path=catalog_path)


@cache
def get_builtin_author_catalog(name: str) -> AuthorCatalog:
    """Load a bundled author collection by normalized name."""
    normalized = normalize_group_name(name)
    try:
        filename = _BUILTIN_CATALOGS[normalized]
    except KeyError as exc:
        available = ", ".join(sorted(_BUILTIN_CATALOGS))
        raise AuthorCatalogError(
            f"unknown built-in author catalog '{name}'; available: {available}"
        ) from exc
    resource = files("litletter").joinpath("data", "author_groups", filename)
    try:
        with resource.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorCatalogError(
            f"could not load built-in author catalog '{normalized}'"
        ) from exc
    return _parse_catalog(payload, path=Path("<builtin>") / normalized)


def author_catalog_template() -> dict[str, Any]:
    """Return a starter catalog containing one editable watchlist."""
    return {
        "version": 2,
        "groups": {
            "watchlist": {
                "description": "Authors whose new papers I always want to see",
                "authors": [
                    {"name": "Ada Lovelace"},
                    {"name": "Grace Hopper"},
                ],
            }
        },
    }


def _parse_catalog(
    payload: Any, *, path: Path = Path("author_groups.json")
) -> AuthorCatalog:
    try:
        version = payload["version"]
        if version not in {1, 2}:
            raise AuthorCatalogError("author_groups.version must be 1 or 2")
        raw_groups = payload["groups"]
        raw_aliases = payload.get("aliases", {})
        unknown_root = set(payload) - {
            "version",
            "groups",
            "aliases",
            "as_of",
            "methodology",
            "source_urls",
        }
        if unknown_root:
            raise AuthorCatalogError(
                "author_groups has unknown keys: " + ", ".join(sorted(unknown_root))
            )
        if not isinstance(raw_groups, dict) or not isinstance(raw_aliases, dict):
            raise TypeError
        if not raw_groups:
            raise AuthorCatalogError("author_groups.groups must not be empty")
        if not all(isinstance(name, str) and name.strip() for name in raw_groups):
            raise AuthorCatalogError("author_groups.groups has invalid names")

        normalized_groups = {
            normalize_group_name(name): group for name, group in raw_groups.items()
        }
        if len(normalized_groups) != len(raw_groups):
            raise AuthorCatalogError("author group names collide after normalization")

        groups: dict[str, AuthorGroup] = {}
        resolving: set[str] = set()

        def resolve(name: str) -> AuthorGroup:
            if name in groups:
                return groups[name]
            if name in resolving:
                raise AuthorCatalogError(f"author group include cycle at '{name}'")
            try:
                raw_group = normalized_groups[name]
            except KeyError as exc:
                raise AuthorCatalogError(
                    f"author group includes unknown group '{name}'"
                ) from exc
            if not isinstance(raw_group, dict):
                raise AuthorCatalogError(f"author group '{name}' must be an object")
            unknown = set(raw_group) - {
                "description",
                "authors",
                "includes",
                "match_initials",
            }
            if unknown:
                unknown_keys = ", ".join(sorted(unknown))
                raise AuthorCatalogError(
                    f"author group '{name}' has unknown keys: {unknown_keys}"
                )

            resolving.add(name)
            raw_authors = raw_group.get("authors", [])
            raw_includes = raw_group.get("includes", [])
            if not isinstance(raw_authors, list) or not isinstance(raw_includes, list):
                raise AuthorCatalogError(
                    f"author group '{name}' authors and includes must be arrays"
                )
            if not raw_authors and not raw_includes:
                raise AuthorCatalogError(f"author group '{name}' must not be empty")
            if not all(
                isinstance(include, str) and include.strip() for include in raw_includes
            ):
                raise AuthorCatalogError(
                    f"author group '{name}' contains invalid includes"
                )

            group_match_initials = raw_group.get("match_initials", version == 1)
            if not isinstance(group_match_initials, bool):
                raise AuthorCatalogError(
                    f"author group '{name}' match_initials must be a boolean"
                )
            authors = [
                _parse_author(
                    author,
                    version=version,
                    group_name=name,
                    default_match_initials=group_match_initials,
                )
                for author in raw_authors
            ]
            for include in raw_includes:
                authors.extend(resolve(normalize_group_name(include)).authors)
            normalized_authors = [_author_identity_key(author) for author in authors]
            if len(set(normalized_authors)) != len(normalized_authors):
                raise AuthorCatalogError(
                    f"author group '{name}' contains duplicate authors"
                )

            description = raw_group.get("description", "")
            if not isinstance(description, str):
                raise AuthorCatalogError(
                    f"author group '{name}' description must be a string"
                )
            group = AuthorGroup(name, description.strip(), tuple(authors))
            groups[name] = group
            resolving.remove(name)
            return group

        for name in normalized_groups:
            resolve(name)

        if not all(
            isinstance(alias, str)
            and alias.strip()
            and isinstance(target, str)
            and target.strip()
            for alias, target in raw_aliases.items()
        ):
            raise AuthorCatalogError("author_groups.aliases is invalid")
        aliases = {
            normalize_group_name(alias): normalize_group_name(target)
            for alias, target in raw_aliases.items()
        }
        if len(aliases) != len(raw_aliases):
            raise AuthorCatalogError("author group aliases collide after normalization")
        missing = set(aliases.values()) - set(groups)
        if missing:
            raise AuthorCatalogError(
                f"author group aliases have missing targets: {sorted(missing)}"
            )

        as_of = payload.get("as_of")
        if as_of is not None:
            if not isinstance(as_of, str):
                raise AuthorCatalogError("author_groups.as_of must be an ISO date")
            try:
                date.fromisoformat(as_of)
            except ValueError as exc:
                raise AuthorCatalogError(
                    "author_groups.as_of must be an ISO date"
                ) from exc
        methodology = payload.get("methodology")
        if methodology is not None and (
            not isinstance(methodology, str) or not methodology.strip()
        ):
            raise AuthorCatalogError(
                "author_groups.methodology must be a non-empty string"
            )
        raw_source_urls = payload.get("source_urls", [])
        if not isinstance(raw_source_urls, list) or not all(
            isinstance(url, str) and url.startswith("https://")
            for url in raw_source_urls
        ):
            raise AuthorCatalogError(
                "author_groups.source_urls must contain HTTPS URLs"
            )
    except (KeyError, TypeError) as exc:
        raise AuthorCatalogError("malformed author-groups catalog") from exc

    return AuthorCatalog(
        path=path,
        groups=MappingProxyType(groups),
        aliases=MappingProxyType(aliases),
        as_of=as_of,
        methodology=methodology.strip() if methodology else None,
        source_urls=tuple(raw_source_urls),
    )


def normalize_group_name(value: str) -> str:
    """Normalize a group name for lookup."""
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def normalize_author(value: str) -> str:
    """Normalize an author-list entry for duplicate detection."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    folded = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(folded.split())


def _parse_author(
    value: Any,
    *,
    version: int,
    group_name: str,
    default_match_initials: bool,
) -> AuthorIdentity:
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if _ORCID.fullmatch(text):
            return AuthorIdentity(
                name=None,
                orcid=text.upper(),
                match_initials=default_match_initials,
            )
        return AuthorIdentity(name=text, match_initials=default_match_initials)
    if version == 1 or not isinstance(value, dict):
        raise AuthorCatalogError(f"author group '{group_name}' has an invalid author")
    unknown = set(value) - {
        "name",
        "orcid",
        "aliases",
        "institution",
        "match_initials",
    }
    if unknown:
        unknown_keys = ", ".join(sorted(unknown))
        raise AuthorCatalogError(
            f"author group '{group_name}' author has unknown keys: {unknown_keys}"
        )

    name = _optional_nonempty_string(value.get("name"), "name", group_name)
    orcid = _optional_nonempty_string(value.get("orcid"), "orcid", group_name)
    if orcid is not None:
        orcid = orcid.removeprefix("https://orcid.org/").upper()
        if not _ORCID.fullmatch(orcid):
            raise AuthorCatalogError(
                f"author group '{group_name}' contains an invalid ORCID"
            )
    raw_aliases = value.get("aliases", [])
    if not isinstance(raw_aliases, list) or not all(
        isinstance(alias, str) and alias.strip() for alias in raw_aliases
    ):
        raise AuthorCatalogError(
            f"author group '{group_name}' author aliases must be an array of strings"
        )
    aliases = tuple(alias.strip() for alias in raw_aliases)
    if len({normalize_author(alias) for alias in aliases}) != len(aliases):
        raise AuthorCatalogError(
            f"author group '{group_name}' author contains duplicate aliases"
        )
    institution = _optional_nonempty_string(
        value.get("institution"), "institution", group_name
    )
    match_initials = value.get("match_initials", default_match_initials)
    if not isinstance(match_initials, bool):
        raise AuthorCatalogError(
            f"author group '{group_name}' author match_initials must be a boolean"
        )
    try:
        return AuthorIdentity(name, orcid, aliases, institution, match_initials)
    except ValueError as exc:
        raise AuthorCatalogError(f"author group '{group_name}': {exc}") from exc


def _optional_nonempty_string(value: Any, field: str, group_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AuthorCatalogError(
            f"author group '{group_name}' author {field} must be a non-empty string"
        )
    return value.strip()


def _author_identity_key(author: AuthorIdentity) -> tuple[str | None, str | None]:
    return (
        normalize_author(author.name) if author.name else None,
        author.orcid.casefold() if author.orcid else None,
    )
