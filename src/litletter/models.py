"""Source-independent domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class PaperSource(StrEnum):
    """A literature source supported by Litletter."""

    PUBMED = "pubmed"
    BIORXIV = "biorxiv"
    MEDRXIV = "medrxiv"
    ARXIV = "arxiv"


class ResearchStatus(StrEnum):
    """How confidently source metadata identifies a paper as research."""

    ORIGINAL = "original"
    NON_RESEARCH = "non_research"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class Author:
    """A normalized paper author."""

    name: str
    orcid: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("author name must not be empty")


@dataclass(frozen=True, slots=True)
class Paper:
    """A paper represented independently of its source API."""

    source: PaperSource
    source_id: str
    title: str
    abstract: str | None
    authors: tuple[Author, ...]
    published_at: date | None
    updated_at: date | None
    doi: str | None
    url: str
    journal: str | None = None
    category: str | None = None
    version: int | None = None
    journal_abbreviation: str | None = None
    journal_nlm_id: str | None = None
    journal_issns: tuple[str, ...] = ()
    publication_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("paper source_id must not be empty")
        if not self.title.strip():
            raise ValueError("paper title must not be empty")
        if not self.url.strip():
            raise ValueError("paper URL must not be empty")

    @property
    def research_status(self) -> ResearchStatus:
        """Classify the paper using source-supplied publication metadata."""
        if self.source in {
            PaperSource.BIORXIV,
            PaperSource.MEDRXIV,
            PaperSource.ARXIV,
        }:
            return ResearchStatus.ORIGINAL
        normalized = {value.casefold().strip() for value in self.publication_types}
        if normalized & _NON_RESEARCH_PUBLICATION_TYPES:
            return ResearchStatus.NON_RESEARCH
        if normalized & _ORIGINAL_RESEARCH_PUBLICATION_TYPES:
            return ResearchStatus.ORIGINAL
        return ResearchStatus.UNCERTAIN

    @property
    def is_original_research(self) -> bool:
        """Apply the conservative research filter, retaining uncertain papers."""
        return self.research_status is not ResearchStatus.NON_RESEARCH


_ORIGINAL_RESEARCH_PUBLICATION_TYPES = {
    "adaptive clinical trial",
    "case reports",
    "clinical study",
    "clinical trial",
    "clinical trial, phase i",
    "clinical trial, phase ii",
    "clinical trial, phase iii",
    "clinical trial, phase iv",
    "comparative study",
    "controlled clinical trial",
    "evaluation study",
    "multicenter study",
    "observational study",
    "observational study, veterinary",
    "pragmatic clinical trial",
    "randomized controlled trial",
    "twin study",
    "validation study",
}

_NON_RESEARCH_PUBLICATION_TYPES = {
    "address",
    "bibliography",
    "biography",
    "comment",
    "conference proceedings",
    "consensus development conference",
    "consensus development conference, nih",
    "consensus statement",
    "corrected and republished article",
    "dataset",
    "duplicate publication",
    "editorial",
    "expression of concern",
    "festschrift",
    "guideline",
    "historical article",
    "interview",
    "lecture",
    "letter",
    "meta-analysis",
    "news",
    "newspaper article",
    "patient education handout",
    "practice guideline",
    "published erratum",
    "retracted publication",
    "retraction notice",
    "retraction of publication",
    "review",
    "systematic review",
}
