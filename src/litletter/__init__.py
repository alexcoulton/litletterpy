"""Fetch and normalize papers for a personal literature newsletter."""

from litletter.discovery import discover_papers
from litletter.errors import (
    JournalCatalogError,
    QuerySyntaxError,
    UnknownJournalGroupError,
)
from litletter.journals import JournalCatalog, JournalGroup, get_journal_catalog
from litletter.models import Author, Paper, PaperSource
from litletter.query import Query, filter_papers, parse_query

__all__ = [
    "Author",
    "JournalCatalog",
    "JournalCatalogError",
    "JournalGroup",
    "Paper",
    "PaperSource",
    "Query",
    "QuerySyntaxError",
    "UnknownJournalGroupError",
    "discover_papers",
    "filter_papers",
    "get_journal_catalog",
    "parse_query",
]
