"""Fetch and normalize papers for a personal literature newsletter."""

from litletter.discovery import discover_papers
from litletter.errors import QuerySyntaxError
from litletter.models import Author, Paper, PaperSource
from litletter.query import Query, filter_papers, parse_query

__all__ = [
    "Author",
    "Paper",
    "PaperSource",
    "Query",
    "QuerySyntaxError",
    "discover_papers",
    "filter_papers",
    "parse_query",
]
