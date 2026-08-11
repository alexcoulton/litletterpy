from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import pytest

from litletter import Paper, PaperSource, discover_papers
from litletter.author_groups import _parse_catalog
from litletter.query import compile_pubmed_candidate_query, parse_query


def paper(
    source: PaperSource,
    source_id: str,
    title: str,
    abstract: str | None,
    published_at: date,
) -> Paper:
    return Paper(
        source=source,
        source_id=source_id,
        title=title,
        abstract=abstract,
        authors=(),
        published_at=published_at,
        updated_at=None,
        doi=None,
        url=f"https://example.test/{source_id}",
    )


@dataclass
class FakePubMed:
    candidates: list[Paper]
    searches: list[tuple[str, date | None, date | None, int | None]] = field(
        default_factory=list
    )
    fetches: list[tuple[date, date, int | None]] = field(default_factory=list)

    def search(
        self,
        query: str,
        *,
        since: date | None = None,
        until: date | None = None,
        max_results: int | None = None,
    ) -> list[Paper]:
        self.searches.append((query, since, until, max_results))
        return self.candidates

    def fetch(
        self,
        *,
        since: date,
        until: date,
        max_results: int | None = None,
    ) -> list[Paper]:
        self.fetches.append((since, until, max_results))
        return self.candidates


@dataclass
class FakeBioRxiv:
    candidates: list[Paper]
    fetches: list[tuple[date, date, int | None]] = field(default_factory=list)

    def fetch(
        self,
        *,
        since: date,
        until: date,
        max_results: int | None = None,
    ) -> list[Paper]:
        self.fetches.append((since, until, max_results))
        return self.candidates


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("title:cancer AND NOT abstract:review", "cancer[Title]"),
        (
            '(title:cancer OR title:tumour) AND abstract:"single cell"',
            "((cancer[Title] OR tumour[Title]) AND "
            "(single[Abstract] AND cell[Abstract]))",
        ),
        (
            "single-cell",
            "(single[Title/Abstract] AND cell[Title/Abstract])",
        ),
        ("title:cancer OR NOT review", None),
        ("NOT review", None),
        ('"***"', None),
        ("journal:Nature", '"Nature"[Journal]'),
        (
            "journal:(Nature OR Science OR Cell)",
            '(("Nature"[Journal] OR "Science"[Journal]) OR "Cell"[Journal])',
        ),
        (
            "journal_group:flagship_nsc",
            '("Nature"[Journal] OR "Science"[Journal] OR "Cell"[Journal])',
        ),
        ("category:bioinformatics", None),
        ("author:Coulton", None),
        ("author:'Alex Coulton'", '"coulton"[Author]'),
        (
            "journal:Nature OR author:'Alex Coulton'",
            '("Nature"[Journal] OR "coulton"[Author])',
        ),
        ("publication_type:original_research", None),
        (
            'publication_type:"Randomized Controlled Trial"',
            '"Randomized Controlled Trial"[Publication Type]',
        ),
    ],
)
def test_compile_pubmed_candidate_query(query: str, expected: str | None) -> None:
    assert compile_pubmed_candidate_query(query) == expected
    assert compile_pubmed_candidate_query(parse_query(query)) == expected


def test_author_group_compiles_to_pubmed_author_selector() -> None:
    catalog = _parse_catalog(
        {
            "version": 1,
            "groups": {"watchlist": {"authors": ["Alex Coulton", "Jane Smith"]}},
        }
    )
    query = parse_query("author_group:watchlist", author_catalog=catalog)

    assert compile_pubmed_candidate_query(query) == (
        '("coulton"[Author] OR "smith"[Author])'
    )


def test_discovery_fetches_both_sources_then_filters_and_sorts() -> None:
    start = date(2026, 8, 1)
    end = date(2026, 8, 9)
    pubmed = FakePubMed(
        [
            paper(
                PaperSource.PUBMED,
                "pm-match",
                "A cancer atlas",
                "An experiment",
                date(2026, 8, 7),
            ),
            paper(
                PaperSource.PUBMED,
                "pm-review",
                "A cancer atlas",
                "A review",
                date(2026, 8, 9),
            ),
        ]
    )
    biorxiv = FakeBioRxiv(
        [
            paper(
                PaperSource.BIORXIV,
                "bio-match",
                "Cancer in model organisms",
                None,
                date(2026, 8, 8),
            ),
            paper(
                PaperSource.BIORXIV,
                "bio-other",
                "A plant atlas",
                None,
                date(2026, 8, 9),
            ),
        ]
    )

    matches = discover_papers(
        "title:cancer AND NOT abstract:review",
        since=start,
        until=end,
        pubmed=pubmed,
        biorxiv=biorxiv,
        max_pubmed_candidates=50,
        max_biorxiv_candidates=75,
    )

    assert [match.source_id for match in matches] == ["bio-match", "pm-match"]
    assert pubmed.searches == [("cancer[Title]", start, end, 50)]
    assert pubmed.fetches == []
    assert biorxiv.fetches == [(start, end, 75)]


def test_discovery_logs_source_progress(caplog: pytest.LogCaptureFixture) -> None:
    today = date(2026, 8, 9)
    caplog.set_level(logging.INFO, logger="litletter.discovery")

    discover_papers(
        "cancer",
        since=today,
        until=today,
        pubmed=FakePubMed([]),
        biorxiv=FakeBioRxiv([]),
    )

    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "Discovering papers from 2026-08-09 through 2026-08-09 using "
        "PubMed and bioRxiv",
        "Fetching PubMed candidates with selector: cancer[Title/Abstract]",
        "PubMed fetched 0 candidates; 0 matched locally",
        "Fetching bioRxiv candidates by date",
        "bioRxiv fetched 0 candidates; 0 matched locally",
        "Discovery complete: 0 matching papers",
    ]


def test_discovery_summarizes_large_journal_group_selector(
    caplog: pytest.LogCaptureFixture,
) -> None:
    today = date(2026, 8, 9)
    caplog.set_level(logging.INFO, logger="litletter.discovery")

    discover_papers(
        "journal_group:nature_index",
        since=today,
        until=today,
        pubmed=FakePubMed([]),
    )

    messages = [record.getMessage() for record in caplog.records]
    selector_message = messages[1]
    assert selector_message.startswith(
        "Fetching PubMed candidates with compiled selector ("
    )
    assert selector_message.endswith(" characters)")


def test_discovery_uses_date_only_pubmed_fetch_without_positive_selector() -> None:
    start = date(2026, 8, 9)
    pubmed = FakePubMed(
        [
            paper(
                PaperSource.PUBMED,
                "original",
                "An original experiment",
                None,
                start,
            ),
            paper(PaperSource.PUBMED, "review", "A review", None, start),
        ]
    )

    matches = discover_papers(
        "NOT review",
        since=start,
        until=start,
        pubmed=pubmed,
    )

    assert [match.source_id for match in matches] == ["original"]
    assert pubmed.searches == []
    assert pubmed.fetches == [(start, start, None)]


def test_discovery_fetches_and_filters_medrxiv_by_date() -> None:
    today = date(2026, 8, 9)
    medrxiv = FakeBioRxiv(
        [
            paper(
                PaperSource.MEDRXIV,
                "med-match",
                "A cancer preprint",
                "Original research",
                today,
            ),
            paper(
                PaperSource.MEDRXIV,
                "med-other",
                "A cardiovascular preprint",
                "Original research",
                today,
            ),
        ]
    )

    matches = discover_papers(
        "title:cancer",
        since=today,
        until=today,
        medrxiv=medrxiv,
        max_medrxiv_candidates=25,
    )

    assert [match.source_id for match in matches] == ["med-match"]
    assert medrxiv.fetches == [(today, today, 25)]


def test_discovery_compiles_arxiv_selector_then_filters_locally() -> None:
    today = date(2026, 8, 9)
    arxiv = FakePubMed(
        [
            paper(
                PaperSource.ARXIV,
                "arxiv-match",
                "A cancer atlas",
                "Original experiment",
                today,
            ),
            paper(
                PaperSource.ARXIV,
                "arxiv-review",
                "A cancer atlas",
                "A review",
                today,
            ),
        ]
    )

    matches = discover_papers(
        "title:cancer AND NOT abstract:review",
        since=today,
        until=today,
        arxiv=arxiv,
        max_arxiv_candidates=50,
    )

    assert [match.source_id for match in matches] == ["arxiv-match"]
    assert arxiv.searches == [("ti:cancer", today, today, 50)]
    assert arxiv.fetches == []


def test_discovery_uses_date_only_arxiv_fetch_without_positive_selector() -> None:
    today = date(2026, 8, 9)
    arxiv = FakePubMed(
        [
            paper(
                PaperSource.ARXIV,
                "original",
                "An original experiment",
                None,
                today,
            ),
            paper(PaperSource.ARXIV, "review", "A review", None, today),
        ]
    )

    matches = discover_papers(
        "NOT review",
        since=today,
        until=today,
        arxiv=arxiv,
    )

    assert [match.source_id for match in matches] == ["original"]
    assert arxiv.searches == []
    assert arxiv.fetches == [(today, today, None)]


def test_discovery_requires_a_source_and_valid_arguments() -> None:
    today = date(2026, 8, 9)

    with pytest.raises(ValueError, match="at least one source"):
        discover_papers("cancer", since=today, until=today)
    with pytest.raises(ValueError, match="since must not"):
        discover_papers(
            "cancer",
            since=today,
            until=date(2026, 8, 8),
            pubmed=FakePubMed([]),
        )
    with pytest.raises(ValueError, match="max_pubmed_candidates"):
        discover_papers(
            "cancer",
            since=today,
            until=today,
            pubmed=FakePubMed([]),
            max_pubmed_candidates=-1,
        )
    with pytest.raises(ValueError, match="max_arxiv_candidates"):
        discover_papers(
            "cancer",
            since=today,
            until=today,
            arxiv=FakePubMed([]),
            max_arxiv_candidates=-1,
        )
