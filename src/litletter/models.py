"""Source-independent domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class PaperSource(StrEnum):
    """A literature source supported by Litletter."""

    PUBMED = "pubmed"
    BIORXIV = "biorxiv"


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

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("paper source_id must not be empty")
        if not self.title.strip():
            raise ValueError("paper title must not be empty")
        if not self.url.strip():
            raise ValueError("paper URL must not be empty")
