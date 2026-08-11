"""Normalize author names for local matching and upstream candidate searches."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from litletter.models import Author

_NAME_TOKEN = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})


def matches_author(author: Author, term: str, *, phrase: bool) -> bool:
    """Return whether an author name or ORCID matches a query term."""
    values = (author.name, author.orcid or "")
    if any(_contains(value, term, phrase=phrase) for value in values):
        return True
    return phrase and _same_person(author.name, term)


def matches_author_identity(
    author: Author,
    name: str,
    *,
    aliases: tuple[str, ...],
    orcid: str | None,
    match_initials: bool,
) -> bool:
    """Match one structured watchlist identity against a paper author."""
    if author.orcid and orcid:
        return _normalize_orcid(author.orcid) == _normalize_orcid(orcid)
    return any(
        _contains(author.name, candidate, phrase=True)
        or _same_person(author.name, candidate, allow_initials=match_initials)
        for candidate in (name, *aliases)
    )


def candidate_family_name(value: str) -> str | None:
    """Return a likely family name from a full natural or comma-inverted name.

    A single-token value is ambiguous: it may be a given name under Litletter's
    local substring semantics, so it cannot safely narrow an upstream search.
    """
    family, given = _identity(value)
    if not family or not given:
        return None
    return " ".join(family)


def _same_person(left: str, right: str, *, allow_initials: bool = True) -> bool:
    left_family, left_given = _identity(left)
    right_family, right_given = _identity(right)
    if not left_family or not right_family or left_family != right_family:
        return False
    if not left_given or not right_given:
        return False
    if not allow_initials and (len(left_given[0]) == 1 or len(right_given[0]) == 1):
        return False
    return _given_name_matches(left_given[0], right_given[0])


def _identity(value: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if "," in value:
        family_text, given_text = value.split(",", 1)
        return _tokens(family_text), _without_suffix(_tokens(given_text))

    tokens = _without_suffix(_tokens(value))
    if len(tokens) < 2:
        return (), ()
    return (tokens[-1],), tokens[:-1]


def _tokens(value: str) -> tuple[str, ...]:
    normalized = _fold(value).replace("\u2019", "'")
    return tuple(_NAME_TOKEN.findall(normalized))


def _without_suffix(tokens: tuple[str, ...]) -> tuple[str, ...]:
    if tokens and tokens[-1] in _SUFFIXES:
        return tokens[:-1]
    return tokens


def _given_name_matches(left: str, right: str) -> bool:
    return (
        left == right
        or (len(left) == 1 and right.startswith(left))
        or (len(right) == 1 and left.startswith(right))
    )


def _contains(value: str, term: str, *, phrase: bool) -> bool:
    normalized_value = " ".join(_fold(value).split())
    normalized_term = " ".join(_fold(term).split())
    if phrase:
        return normalized_term in normalized_value
    return _word_pattern(normalized_term).search(normalized_value) is not None


def _normalize_orcid(value: str) -> str:
    return value.casefold().removeprefix("https://orcid.org/")


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )


@lru_cache(maxsize=256)
def _word_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
