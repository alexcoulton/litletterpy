from __future__ import annotations

import json
from pathlib import Path

import pytest

from litletter.author_groups import _parse_catalog, load_author_catalog
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
        "Alex Coulton",
        "Jane Smith",
        "0000-0001-2345-6789",
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
