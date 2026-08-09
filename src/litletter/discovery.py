"""Orchestrate source fetching and local query evaluation."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Protocol

from litletter.models import Paper
from litletter.query import (
    Query,
    compile_pubmed_candidate_query,
    filter_papers,
    parse_query,
)


class _PubMedSource(Protocol):
    def search(
        self,
        query: str,
        *,
        since: date | None = None,
        until: date | None = None,
        max_results: int | None = None,
    ) -> list[Paper]: ...

    def fetch(
        self,
        *,
        since: date,
        until: date,
        max_results: int | None = None,
    ) -> list[Paper]: ...


class _BioRxivSource(Protocol):
    def fetch(
        self,
        *,
        since: date,
        until: date,
        max_results: int | None = None,
    ) -> list[Paper]: ...


def discover_papers(
    query: Query | str,
    *,
    since: date,
    until: date,
    pubmed: _PubMedSource | None = None,
    biorxiv: _BioRxivSource | None = None,
    max_pubmed_candidates: int | None = None,
    max_biorxiv_candidates: int | None = None,
) -> list[Paper]:
    """Fetch and locally match papers from the supplied source clients.

    At least one source client is required. Candidate limits apply before local
    filtering and are intended for previews or bounded test runs.
    """
    if pubmed is None and biorxiv is None:
        raise ValueError("at least one source client is required")
    if since > until:
        raise ValueError("since must not be after until")
    _validate_limit("max_pubmed_candidates", max_pubmed_candidates)
    _validate_limit("max_biorxiv_candidates", max_biorxiv_candidates)

    parsed = parse_query(query) if isinstance(query, str) else query
    matches: list[Paper] = []

    if pubmed is not None:
        selector = compile_pubmed_candidate_query(parsed)
        if selector is None:
            candidates = pubmed.fetch(
                since=since,
                until=until,
                max_results=max_pubmed_candidates,
            )
        else:
            candidates = pubmed.search(
                selector,
                since=since,
                until=until,
                max_results=max_pubmed_candidates,
            )
        matches.extend(filter_papers(candidates, parsed))

    if biorxiv is not None:
        candidates = biorxiv.fetch(
            since=since,
            until=until,
            max_results=max_biorxiv_candidates,
        )
        matches.extend(filter_papers(candidates, parsed))

    return _sort_papers(matches)


def _sort_papers(papers: Iterable[Paper]) -> list[Paper]:
    return sorted(
        papers,
        key=lambda paper: (
            paper.published_at or date.min,
            paper.source.value,
            paper.source_id,
        ),
        reverse=True,
    )


def _validate_limit(name: str, value: int | None) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{name} must not be negative")
