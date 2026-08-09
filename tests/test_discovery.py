from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest

from litletter import Paper, PaperSource, discover_papers
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
    ],
)
def test_compile_pubmed_candidate_query(query: str, expected: str | None) -> None:
    assert compile_pubmed_candidate_query(query) == expected
    assert compile_pubmed_candidate_query(parse_query(query)) == expected


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
