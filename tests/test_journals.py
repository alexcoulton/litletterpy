from __future__ import annotations

from datetime import date

import pytest

from litletter import parse_query
from litletter.errors import JournalCatalogError, UnknownJournalGroupError
from litletter.journals import _parse_catalog, get_journal_catalog
from litletter.models import Paper, PaperSource


def paper(*, journal: str, abbreviation: str | None = None) -> Paper:
    return Paper(
        source=PaperSource.PUBMED,
        source_id="1",
        title="A result",
        abstract=None,
        authors=(),
        published_at=date(2026, 8, 9),
        updated_at=None,
        doi=None,
        url="https://example.test/1",
        journal=journal,
        journal_abbreviation=abbreviation,
    )


def test_bundled_catalog_has_versioned_sourced_collections() -> None:
    catalog = get_journal_catalog()

    assert len(catalog.get("flagship_nsc").journals) == 3
    assert len(catalog.get("science_family").journals) == 6
    assert len(catalog.get("cell_press").journals) == 55
    assert len(catalog.get("nature_portfolio").journals) == 173
    assert len(catalog.get("nature_index_2026").journals) == 177
    assert catalog.get("nature_index_current") is catalog.get("nature_index_2026")
    assert all(
        group.source_url.startswith("https://") for group in catalog.groups.values()
    )
    assert all(group.as_of == "2026-08-09" for group in catalog.groups.values())


def test_catalog_membership_accepts_title_or_pubmed_abbreviation() -> None:
    catalog = get_journal_catalog()

    assert catalog.contains("flagship_nsc", paper(journal="Nature"))
    assert catalog.contains(
        "flagship_nsc", paper(journal="Unexpanded title", abbreviation="Science")
    )
    assert not catalog.contains("flagship_nsc", paper(journal="Nature Medicine"))


def test_unknown_group_has_clear_error_and_available_names() -> None:
    with pytest.raises(
        UnknownJournalGroupError, match="unknown journal group"
    ) as error:
        get_journal_catalog().get("not-a-group")

    assert "flagship_nsc" in str(error.value)


def test_unknown_group_fails_during_query_evaluation() -> None:
    with pytest.raises(UnknownJournalGroupError):
        parse_query("journal_group:not_a_group").matches(paper(journal="Nature"))


def test_catalog_parser_flattens_included_groups() -> None:
    metadata = {
        "description": "Test collection",
        "source_url": "https://example.test/journals",
        "as_of": "2026-08-09",
    }
    catalog = _parse_catalog(
        {
            "schema_version": 1,
            "groups": {
                "first": {**metadata, "journals": ["One", "Two"]},
                "second": {**metadata, "journals": ["Three"]},
                "combined": {
                    **metadata,
                    "includes": ["first", "second"],
                },
            },
            "aliases": {"all": "combined"},
        }
    )

    assert catalog.get("all").journals == ("One", "Two", "Three")


@pytest.mark.parametrize(
    "groups",
    [
        {},
        {
            "empty": {
                "description": "Empty",
                "source_url": "https://example.test",
                "as_of": "2026-08-09",
            }
        },
        {
            "loop": {
                "description": "Loop",
                "source_url": "https://example.test",
                "as_of": "2026-08-09",
                "includes": ["loop"],
            }
        },
    ],
)
def test_catalog_parser_rejects_invalid_groups(groups: dict) -> None:
    with pytest.raises(JournalCatalogError):
        _parse_catalog({"schema_version": 1, "groups": groups})
