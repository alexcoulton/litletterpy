"""Parse and evaluate source-independent Litletter queries."""

from litletter.errors import QuerySyntaxError
from litletter.query._ast import And, Expression, Field, Not, Or, Query, Term
from litletter.query._evaluator import evaluate, filter_papers
from litletter.query._parser import parse_query
from litletter.query._pubmed import compile_pubmed_candidate_query

__all__ = [
    "And",
    "Expression",
    "Field",
    "Not",
    "Or",
    "Query",
    "QuerySyntaxError",
    "Term",
    "compile_pubmed_candidate_query",
    "evaluate",
    "filter_papers",
    "parse_query",
]
