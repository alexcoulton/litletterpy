"""Abstract syntax tree for Litletter queries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from litletter.models import Paper


class Field(StrEnum):
    """A paper text field that a query term can search."""

    TITLE = "title"
    ABSTRACT = "abstract"
    TITLE_ABSTRACT = "title_abstract"
    JOURNAL = "journal"
    JOURNAL_GROUP = "journal_group"
    CATEGORY = "category"
    AUTHOR = "author"
    PUBLICATION_TYPE = "publication_type"


class Expression:
    """Base class for query expressions."""


@dataclass(frozen=True, slots=True)
class Term(Expression):
    """A word or quoted phrase searched within a paper field."""

    text: str
    field: Field = Field.TITLE_ABSTRACT
    phrase: bool = False

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("query term must not be empty")


@dataclass(frozen=True, slots=True)
class Not(Expression):
    """Negate an expression."""

    operand: Expression


@dataclass(frozen=True, slots=True)
class And(Expression):
    """Require both expressions to match."""

    left: Expression
    right: Expression


@dataclass(frozen=True, slots=True)
class Or(Expression):
    """Require either expression to match."""

    left: Expression
    right: Expression


@dataclass(frozen=True, slots=True)
class Query:
    """A parsed Litletter query that can be reused across papers."""

    text: str
    root: Expression

    def matches(self, paper: Paper) -> bool:
        """Return whether ``paper`` satisfies this query."""
        from litletter.query._evaluator import evaluate

        return evaluate(self.root, paper)
