"""Fetch and normalize papers for a personal literature newsletter."""

from litletter.author_groups import (
    AuthorCatalog,
    AuthorGroup,
    load_author_catalog,
)
from litletter.discovery import discover_papers
from litletter.errors import (
    AuthorCatalogError,
    JournalCatalogError,
    QuerySyntaxError,
    UnknownAuthorGroupError,
    UnknownJournalGroupError,
)
from litletter.journals import JournalCatalog, JournalGroup, get_journal_catalog
from litletter.models import Author, Paper, PaperSource
from litletter.query import Query, filter_papers, parse_query

__all__ = [
    "Author",
    "AuthorCatalog",
    "AuthorCatalogError",
    "AuthorGroup",
    "JournalCatalog",
    "JournalCatalogError",
    "JournalGroup",
    "Paper",
    "PaperSource",
    "Query",
    "QuerySyntaxError",
    "UnknownAuthorGroupError",
    "UnknownJournalGroupError",
    "discover_papers",
    "filter_papers",
    "get_journal_catalog",
    "load_author_catalog",
    "parse_query",
]
