"""Compile Litletter queries into broad arXiv candidate selectors."""

from __future__ import annotations

import re

from litletter.query._ast import And, Expression, Field, Not, Or, Query, Term
from litletter.query._parser import parse_query

_SEARCH_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_CATEGORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def compile_arxiv_candidate_query(query: Query | str) -> str | None:
    """Return a broad arXiv selector, or ``None`` when none is safe.

    This selector only reduces the candidate set. Negated and source-irrelevant
    fields are omitted, and the complete Litletter query must still be applied
    locally to normalized results.
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
    if term.field is Field.CATEGORY:
        return f"cat:{term.text}" if _CATEGORY.fullmatch(term.text) else None
    if term.field in {
        Field.JOURNAL,
        Field.JOURNAL_GROUP,
        Field.PUBLICATION_TYPE,
    }:
        return None

    tokens = _SEARCH_TOKEN.findall(term.text)
    if not tokens:
        return None
    components = [_compile_text_token(token, term.field) for token in tokens]
    if len(components) == 1:
        return components[0]
    return f"({' AND '.join(components)})"


def _compile_text_token(token: str, field: Field) -> str:
    if field is Field.TITLE:
        return f"ti:{token}"
    if field is Field.ABSTRACT:
        return f"abs:{token}"
    if field is Field.TITLE_ABSTRACT:
        return f"(ti:{token} OR abs:{token})"
    raise TypeError(f"unsupported text field: {field.value}")
