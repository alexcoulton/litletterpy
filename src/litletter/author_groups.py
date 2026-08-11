"""User-owned named author collections used by Litletter queries."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from litletter.errors import AuthorCatalogError, UnknownAuthorGroupError


@dataclass(frozen=True, slots=True)
class AuthorGroup:
    """A named collection of author names or ORCIDs."""

    name: str
    description: str
    authors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorCatalog:
    """An immutable catalog loaded from a user's author-groups file."""

    path: Path
    groups: Mapping[str, AuthorGroup]
    aliases: Mapping[str, str]

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


def author_catalog_template() -> dict[str, Any]:
    """Return a starter catalog containing one editable watchlist."""
    return {
        "version": 1,
        "groups": {
            "watchlist": {
                "description": "Authors whose new papers I always want to see",
                "authors": ["Ada Lovelace", "Grace Hopper"],
            }
        },
    }


def _parse_catalog(
    payload: Any, *, path: Path = Path("author_groups.json")
) -> AuthorCatalog:
    try:
        if payload["version"] != 1:
            raise AuthorCatalogError("author_groups.version must be 1")
        raw_groups = payload["groups"]
        raw_aliases = payload.get("aliases", {})
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
            unknown = set(raw_group) - {"description", "authors", "includes"}
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
                isinstance(author, str) and author.strip() for author in raw_authors
            ):
                raise AuthorCatalogError(
                    f"author group '{name}' contains invalid authors"
                )
            if not all(
                isinstance(include, str) and include.strip() for include in raw_includes
            ):
                raise AuthorCatalogError(
                    f"author group '{name}' contains invalid includes"
                )

            authors = [author.strip() for author in raw_authors]
            for include in raw_includes:
                authors.extend(resolve(normalize_group_name(include)).authors)
            normalized_authors = [normalize_author(author) for author in authors]
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
    except (KeyError, TypeError) as exc:
        raise AuthorCatalogError("malformed author-groups catalog") from exc

    return AuthorCatalog(
        path=path,
        groups=MappingProxyType(groups),
        aliases=MappingProxyType(aliases),
    )


def normalize_group_name(value: str) -> str:
    """Normalize a group name for lookup."""
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def normalize_author(value: str) -> str:
    """Normalize an author-list entry for duplicate detection."""
    return " ".join(value.casefold().split())
