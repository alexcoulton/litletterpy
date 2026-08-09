"""Evaluate Litletter query expressions against normalized papers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from litletter.models import Paper
from litletter.query._ast import And, Expression, Field, Not, Or, Query, Term
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
    return _evaluate(expression, text)


def filter_papers(papers: Iterable[Paper], query: Query | str) -> list[Paper]:
    """Return matching papers while preserving their input order."""
    parsed = parse_query(query) if isinstance(query, str) else query
    return [paper for paper in papers if parsed.matches(paper)]


def _evaluate(expression: Expression, text: _PaperText) -> bool:
    if isinstance(expression, Term):
        return any(
            _contains(value, expression.text, phrase=expression.phrase)
            for value in _field_values(expression.field, text)
        )
    if isinstance(expression, Not):
        return not _evaluate(expression.operand, text)
    if isinstance(expression, And):
        return _evaluate(expression.left, text) and _evaluate(expression.right, text)
    if isinstance(expression, Or):
        return _evaluate(expression.left, text) or _evaluate(expression.right, text)
    raise TypeError(f"unsupported query expression: {type(expression).__name__}")


def _field_values(field: Field, text: _PaperText) -> tuple[str, ...]:
    if field is Field.TITLE:
        return (text.title,)
    if field is Field.ABSTRACT:
        return (text.abstract,)
    return (text.title, text.abstract)


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
