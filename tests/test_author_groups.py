from __future__ import annotations

import json
from pathlib import Path

import pytest

from litletter.author_groups import (
    AuthorIdentity,
    _parse_catalog,
    get_builtin_author_catalog,
    load_author_catalog,
)
from litletter.errors import AuthorCatalogError, UnknownAuthorGroupError


def test_catalog_loads_groups_includes_and_aliases(tmp_path: Path) -> None:
    path = tmp_path / "author_groups.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "groups": {
                    "cancer": {
                        "description": "Cancer researchers",
                        "authors": ["Jane Smith", "0000-0001-2345-6789"],
                    },
                    "all": {
                        "description": "Everyone",
                        "authors": ["Alex Coulton"],
                        "includes": ["cancer"],
                    },
                },
                "aliases": {"watched": "all"},
            }
        ),
        encoding="utf-8",
    )

    catalog = load_author_catalog(path)

    assert catalog.path == path
    assert catalog.names() == ("all", "cancer")
    assert catalog.get("watched").authors == (
        AuthorIdentity("Alex Coulton", match_initials=True),
        AuthorIdentity("Jane Smith", match_initials=True),
        AuthorIdentity(None, "0000-0001-2345-6789", match_initials=True),
    )


def test_version_two_supports_structured_author_identities() -> None:
    catalog = _parse_catalog(
        {
            "version": 2,
            "groups": {
                "watchlist": {
                    "authors": [
                        {
                            "name": "Alex Coulton",
                            "orcid": "https://orcid.org/0000-0001-2345-6789",
                            "aliases": ["Alexander Coulton"],
                            "institution": "Example University",
                        }
                    ]
                }
            },
        }
    )

    assert catalog.get("watchlist").authors == (
        AuthorIdentity(
            "Alex Coulton",
            "0000-0001-2345-6789",
            ("Alexander Coulton",),
            "Example University",
        ),
    )


def test_unknown_group_lists_available_names() -> None:
    catalog = _parse_catalog(
        {
            "version": 1,
            "groups": {"watchlist": {"authors": ["Alex Coulton"]}},
        }
    )

    with pytest.raises(UnknownAuthorGroupError, match="available groups: watchlist"):
        catalog.get("missing")


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "groups": {}},
        {"version": 1, "groups": {"empty": {"authors": []}}},
        {
            "version": 1,
            "groups": {"loop": {"includes": ["loop"]}},
        },
        {
            "version": 1,
            "groups": {"duplicates": {"authors": ["Jane Smith", " jane  smith "]}},
        },
    ],
)
def test_catalog_rejects_invalid_groups(payload: dict) -> None:
    with pytest.raises(AuthorCatalogError):
        _parse_catalog(payload)


def test_curated_cancer_collection_is_valid_and_unique() -> None:
    catalog = get_builtin_author_catalog("cancer-researchers")
    combined = catalog.get("cancer-watchlist")

    assert catalog.as_of == "2026-08-11"
    assert len(catalog.source_urls) == 5
    assert len(combined.authors) == 205
    assert len({author.fingerprint() for author in combined.authors}) == 205
    assert catalog.get("cancer-researchers") is combined
