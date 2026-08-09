"""Compile Litletter queries into broad PubMed candidate selectors."""

from __future__ import annotations

import re

from litletter.query._ast import And, Expression, Field, Not, Or, Query, Term
from litletter.query._parser import parse_query

_SEARCH_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_FIELD_TAGS = {
    Field.TITLE: "[Title]",
    Field.ABSTRACT: "[Abstract]",
    Field.TITLE_ABSTRACT: "[Title/Abstract]",
}


def compile_pubmed_candidate_query(query: Query | str) -> str | None:
    """Return a broad PubMed selector, or ``None`` when none is safe.

    The selector is only an optimization. Negated expressions are removed from
    candidate selection, and phrases or punctuated terms are broadened into
    their component words. The original Litletter query must still be applied
    locally to the returned records.
    """
    parsed = parse_query(query) if isinstance(query, str) else query
    return _compile(parsed.root)


def _compile(expression: Expression) -> str | None:
    if isinstance(expression, Term):
        return _compile_term(expression)
    if isinstance(expression, Not):
        return None
    if isinstance(expression, And):
        left = _compile(expression.left)
        right = _compile(expression.right)
        if left is None:
            return right
        if right is None:
            return left
        return f"({left} AND {right})"
    if isinstance(expression, Or):
        left = _compile(expression.left)
        right = _compile(expression.right)
        if left is None or right is None:
            return None
        return f"({left} OR {right})"
    raise TypeError(f"unsupported query expression: {type(expression).__name__}")


def _compile_term(term: Term) -> str | None:
    tokens = _SEARCH_TOKEN.findall(term.text)
    if not tokens:
        return None
    tag = _FIELD_TAGS[term.field]
    components = [f"{token}{tag}" for token in tokens]
    if len(components) == 1:
        return components[0]
    return f"({' AND '.join(components)})"
