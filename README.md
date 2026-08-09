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
future matching layer will evaluate bioRxiv records locally.
