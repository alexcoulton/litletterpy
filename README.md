# Litletter

Litletter is a local Python package for fetching and normalizing papers from
PubMed and bioRxiv. Boolean matching and newsletter delivery will be added in
later milestones.

## Development

The project uses [uv](https://docs.astral.sh/uv/) to manage its pinned Python
3.12 environment and dependencies:

```console
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Fetching papers

```python
from datetime import date

from litletter.sources import BioRxivClient, PubMedClient

with PubMedClient(email="you@example.com") as pubmed:
    papers = pubmed.search(
        '"single cell"[Title/Abstract] AND cancer[Title/Abstract]',
        since=date(2026, 8, 1),
        until=date(2026, 8, 9),
    )

with BioRxivClient() as biorxiv:
    preprints = biorxiv.fetch(
        since=date(2026, 8, 1),
        until=date(2026, 8, 9),
    )
```

PubMed accepts a native PubMed query. The official bioRxiv API lists records by
date rather than performing Boolean title/abstract searches, so Litletter's
matching layer evaluates normalized records locally.

## Matching papers

Parse a query once and reuse it for papers from either source:

```python
from litletter import filter_papers, parse_query

query = parse_query(
    'title:("spatial transcriptomics" OR single-cell) '
    "AND abstract:(cancer OR tumour) "
    "AND NOT title:review"
)

matching_papers = filter_papers(papers, query)
```

The query language supports:

- Case-insensitive `AND`, `OR`, and unary `NOT`.
- Parentheses, with precedence `NOT`, then `AND`, then `OR`.
- Quoted phrases, including `\"` and `\\` escapes.
- `title:`, `abstract:`, and `title_abstract:` field prefixes.
- Field-scoped groups such as `title:(cancer OR tumour)`.

Unqualified terms search the title and abstract. Unquoted terms match complete
words, while quoted phrases match a contiguous substring. Matching is Unicode
case-insensitive and collapses runs of whitespace. Boolean operators must be
explicit; Litletter does not insert an implicit `AND` between adjacent terms.
