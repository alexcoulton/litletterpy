"""Versioned journal collections used by Litletter queries."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType
from typing import Any

from litletter.errors import JournalCatalogError, UnknownJournalGroupError
from litletter.models import Paper


@dataclass(frozen=True, slots=True)
class JournalGroup:
    """A named, sourced collection of journal titles."""

    name: str
    description: str
    source_url: str
    as_of: str
    journals: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JournalCatalog:
    """An immutable catalog of named journal collections."""

    groups: Mapping[str, JournalGroup]
    aliases: Mapping[str, str]

    def get(self, name: str) -> JournalGroup:
        """Return a group by case-insensitive name or alias."""
        key = _normalize_group_name(name)
        key = self.aliases.get(key, key)
        try:
            return self.groups[key]
        except KeyError as exc:
            raise UnknownJournalGroupError(name, self.names()) from exc

    def names(self) -> tuple[str, ...]:
        """Return canonical group names in alphabetical order."""
        return tuple(sorted(self.groups))

    def contains(self, group_name: str, paper: Paper) -> bool:
        """Return whether a paper's journal belongs to a group."""
        group = self.get(group_name)
        paper_names = {
            _normalize_journal_name(value) for value in journal_aliases(paper)
        }
        return not paper_names.isdisjoint(_normalized_journal_names(group.journals))


def journal_aliases(paper: Paper) -> tuple[str, ...]:
    """Return every journal identity retained on a normalized paper."""
    values = (
        paper.journal,
        paper.journal_abbreviation,
        paper.journal_nlm_id,
        *paper.journal_issns,
    )
    return tuple(value for value in values if value)


def journal_matches(paper: Paper, value: str) -> bool:
    """Match a journal title, abbreviation, NLM ID, or ISSN exactly."""
    expected = _normalize_journal_name(value)
    return any(
        _normalize_journal_name(alias) == expected for alias in journal_aliases(paper)
    )


@lru_cache(maxsize=1)
def get_journal_catalog() -> JournalCatalog:
    """Load and validate Litletter's bundled journal catalog."""
    resource = files("litletter").joinpath("data/journal_groups.json")
    try:
        with resource.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise JournalCatalogError("could not load bundled journal catalog") from exc
    return _parse_catalog(payload)


def _parse_catalog(payload: Any) -> JournalCatalog:
    try:
        if payload["schema_version"] != 1:
            raise JournalCatalogError("unsupported journal catalog schema")
        raw_groups = payload["groups"]
        raw_aliases = payload.get("aliases", {})
        if not isinstance(raw_groups, dict) or not isinstance(raw_aliases, dict):
            raise TypeError
        if not raw_groups:
            raise JournalCatalogError("journal catalog has no groups")
        if not all(isinstance(name, str) and name.strip() for name in raw_groups):
            raise JournalCatalogError("journal catalog has invalid group names")

        normalized_groups = {
            _normalize_group_name(name): group for name, group in raw_groups.items()
        }
        if len(normalized_groups) != len(raw_groups):
            raise JournalCatalogError("journal group names collide after normalization")

        groups: dict[str, JournalGroup] = {}
        resolving: set[str] = set()

        def resolve(name: str) -> JournalGroup:
            if name in groups:
                return groups[name]
            if name in resolving:
                raise JournalCatalogError(f"journal group include cycle at '{name}'")
            try:
                raw_group = normalized_groups[name]
            except KeyError as exc:
                raise JournalCatalogError(
                    f"journal group includes unknown group '{name}'"
                ) from exc
            if not isinstance(raw_group, dict):
                raise JournalCatalogError(f"journal group '{name}' is not an object")

            resolving.add(name)
            raw_journals = raw_group.get("journals", [])
            raw_includes = raw_group.get("includes", [])
            if not isinstance(raw_journals, list) or not isinstance(raw_includes, list):
                raise JournalCatalogError(
                    f"journal group '{name}' journals and includes must be lists"
                )
            direct_journals = tuple(raw_journals)
            includes = tuple(raw_includes)
            if not direct_journals and not includes:
                raise JournalCatalogError(f"journal group '{name}' is empty")
            if not all(
                isinstance(journal, str) and journal.strip()
                for journal in direct_journals
            ):
                raise JournalCatalogError(
                    f"journal group '{name}' has invalid journals"
                )
            if len(
                {_normalize_journal_name(value) for value in direct_journals}
            ) != len(direct_journals):
                raise JournalCatalogError(
                    f"journal group '{name}' has duplicate journals"
                )
            if not all(
                isinstance(include, str) and include.strip() for include in includes
            ):
                raise JournalCatalogError(
                    f"journal group '{name}' has invalid includes"
                )

            journals = list(direct_journals)
            for include in includes:
                included = resolve(_normalize_group_name(include))
                journals.extend(included.journals)
            journals = list(dict.fromkeys(journals))
            if len({_normalize_journal_name(value) for value in journals}) != len(
                journals
            ):
                raise JournalCatalogError(
                    f"journal group '{name}' has journals that collide "
                    "after normalization"
                )

            description = raw_group["description"]
            source_url = raw_group["source_url"]
            as_of = raw_group["as_of"]
            if not all(
                isinstance(value, str) and value.strip()
                for value in (description, source_url, as_of)
            ):
                raise JournalCatalogError(
                    f"journal group '{name}' has invalid metadata"
                )
            date.fromisoformat(as_of)

            group = JournalGroup(
                name=name,
                description=description,
                source_url=source_url,
                as_of=as_of,
                journals=tuple(journals),
            )
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
            raise JournalCatalogError("journal catalog has invalid aliases")
        aliases = {
            _normalize_group_name(alias): _normalize_group_name(target)
            for alias, target in raw_aliases.items()
        }
        if len(aliases) != len(raw_aliases):
            raise JournalCatalogError("journal aliases collide after normalization")
        missing_targets = set(aliases.values()) - set(groups)
        if missing_targets:
            raise JournalCatalogError(
                f"journal aliases have missing targets: {sorted(missing_targets)}"
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise JournalCatalogError("malformed journal catalog") from exc
    return JournalCatalog(
        groups=MappingProxyType(groups), aliases=MappingProxyType(aliases)
    )


def _normalize_group_name(value: str) -> str:
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def _normalize_journal_name(value: str) -> str:
    return " ".join(value.casefold().split())


@lru_cache(maxsize=32)
def _normalized_journal_names(journals: tuple[str, ...]) -> frozenset[str]:
    return frozenset(_normalize_journal_name(journal) for journal in journals)
