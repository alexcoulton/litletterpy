"""Evaluate Litletter query expressions against normalized papers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from litletter.journals import get_journal_catalog, journal_matches
from litletter.models import Paper
from litletter.query._ast import And, Expression, Field, Not, Or, Query, Term
from litletter.query._authors import matches_author, matches_author_identity
from litletter.query._parser import parse_query


@dataclass(frozen=True, slots=True)
class _PaperText:
    title: str
    abstract: str


def evaluate(expression: Expression, paper: Paper) -> bool:
    """Return whether an expression matches a normalized paper."""
    text = _PaperText(
        title=_normalize(paper.title),
        abstract=_normalize(paper.abstract or ""),
    )
    return _evaluate(expression, text, paper)


def filter_papers(papers: Iterable[Paper], query: Query | str) -> list[Paper]:
    """Return matching papers while preserving their input order."""
    parsed = parse_query(query) if isinstance(query, str) else query
    return [paper for paper in papers if parsed.matches(paper)]


def _evaluate(expression: Expression, text: _PaperText, paper: Paper) -> bool:
    if isinstance(expression, Term):
        if expression.field is Field.JOURNAL:
            return journal_matches(paper, expression.text)
        if expression.field is Field.JOURNAL_GROUP:
            return get_journal_catalog().contains(expression.text, paper)
        if expression.field is Field.PUBLICATION_TYPE:
            if _normalize(expression.text) == "original_research":
                return paper.is_original_research
            return any(
                _normalize(value) == _normalize(expression.text)
                for value in paper.publication_types
            )
        if expression.field is Field.AUTHOR:
            if (
                expression.author_aliases
                or expression.author_orcid
                or not expression.author_match_initials
            ):
                return any(
                    matches_author_identity(
                        author,
                        expression.text,
                        aliases=expression.author_aliases,
                        orcid=expression.author_orcid,
                        match_initials=expression.author_match_initials,
                    )
                    for author in paper.authors
                )
            return any(
                matches_author(
                    author,
                    expression.text,
                    phrase=expression.phrase,
                )
                for author in paper.authors
            )
        if expression.field is Field.AUTHOR_GROUP:
            raise AssertionError("author groups must be expanded while parsing")
        return any(
            _contains(value, expression.text, phrase=expression.phrase)
            for value in _field_values(expression.field, text, paper)
        )
    if isinstance(expression, Not):
        return not _evaluate(expression.operand, text, paper)
    if isinstance(expression, And):
        return _evaluate(expression.left, text, paper) and _evaluate(
            expression.right, text, paper
        )
    if isinstance(expression, Or):
        return _evaluate(expression.left, text, paper) or _evaluate(
            expression.right, text, paper
        )
    raise TypeError(f"unsupported query expression: {type(expression).__name__}")


def _field_values(field: Field, text: _PaperText, paper: Paper) -> tuple[str, ...]:
    if field is Field.TITLE:
        return (text.title,)
    if field is Field.ABSTRACT:
        return (text.abstract,)
    if field is Field.CATEGORY:
        return (_normalize(paper.category or ""),)
    if field is Field.TITLE_ABSTRACT:
        return (text.title, text.abstract)
    return ()


def _contains(value: str, term: str, *, phrase: bool) -> bool:
    normalized_term = _normalize(term)
    if phrase:
        return normalized_term in value
    return _word_pattern(normalized_term).search(value) is not None


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


@lru_cache(maxsize=512)
def _word_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
