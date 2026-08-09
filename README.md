# Litletter

Litletter is a local Python package for fetching and normalizing papers from
PubMed and bioRxiv, then applying a shared Boolean query language to their
titles, abstracts, journals, and categories. Its CLI can run those searches on
a schedule, retain delivery state in SQLite, and send a categorized email
newsletter through Postmark without resending previously delivered papers.

## Development

The project uses [uv](https://docs.astral.sh/uv/) to manage its pinned Python
3.12 environment and dependencies:

```console
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Installing the project creates the `litletter` command inside `.venv/bin`.
Either activate the environment or prefix commands with `uv run`.

## Scheduled newsletters

Start from [examples/litletter.json](examples/litletter.json). The configuration
contains addressing, source credentials, discovery-window behavior, ordered
search categories, and delivery settings. Paths such as `database` are resolved
relative to the JSON file, not the shell's current directory.

Each category has a stable lowercase ID, a display name, a Litletter Boolean
query, and one or more sources. For example:

```json
{
  "id": "nsc-cancer",
  "name": "Nature, Science and Cell: Cancer",
  "query": "title_abstract:cancer AND journal_group:flagship_nsc",
  "sources": ["pubmed"]
}
```

Category order controls the email section order. If a paper matches several
categories, it appears once under the first matching category and lists the
others as secondary categories.

Copy and edit the example, then validate it and initialize its SQLite database:

```console
cp examples/litletter.json litletter.json
uv run litletter config validate --config litletter.json
uv run litletter db init --config litletter.json
```

The first date window must be approved explicitly. Previewing it is the safest
bootstrap workflow:

```console
uv run litletter run --config litletter.json \
  --bootstrap --dry-run --output /tmp/litletter-preview.html
```

That command fetches and stores matches and advances the discovery watermark,
but does not create a delivery edition or mark any paper sent. Subsequent runs
use an overlapping date window so delayed indexing does not create gaps. Global
deduplication happens against successfully submitted editions, so overlap and
papers matching multiple categories do not cause repeated email entries.

### Postmark delivery

Create a Postmark server, verify the individual address used by
`newsletter.from`, and create a Broadcast Message Stream whose ID matches
`delivery.message_stream`. Put its server token in the environment variable
named by `delivery.token_env`:

```console
export LITLETTER_POSTMARK_TOKEN="your-server-token"
uv run litletter run --config litletter.json
```

A verified individual sender signature is sufficient for this single-user
setup; the sender and recipient can be the same address. A Broadcast stream is
appropriate for newsletters and lets Postmark apply its broadcast/unsubscribe
handling.

Use `litletter status --config litletter.json` to inspect the watermark, pending
papers, submitted editions, and any edition requiring attention. Provider
requests are deliberately not retried automatically. A definite rejection
leaves a failed edition that can be resent after correction:

```console
uv run litletter run --config litletter.json --retry-open-edition
```

If a timeout makes the outcome uncertain, the edition remains in `sending` and
all future sends stop. Check Postmark using the edition metadata shown by
`litletter status`, then resolve it explicitly:

```console
# The message exists in Postmark:
uv run litletter edition resolve --config litletter.json EDITION_ID \
  --delivered --message-id POSTMARK_MESSAGE_ID

# Postmark confirms that it was not accepted:
uv run litletter edition resolve --config litletter.json EDITION_ID \
  --not-delivered
uv run litletter run --config litletter.json --retry-open-edition
```

### Scheduling on a server

One CLI invocation performs one finite run, which makes it suitable for cron or
a systemd timer. It takes a non-blocking file lock next to the database, so an
overlapping invocation exits instead of running concurrently.

For cron, activate nothing; call the installed executable by its absolute path:

```cron
15 7 * * * cd /opt/litletter && /opt/litletter/.venv/bin/litletter run --config /etc/litletter/litletter.json
```

The sample [systemd service](deploy/litletter.service) and
[timer](deploy/litletter.timer) are more resilient: the timer is persistent, so
a run missed while the server was offline starts after it returns. Adapt the
paths, copy the JSON to `/etc/litletter/litletter.json`, use
`/var/lib/litletter/litletter.sqlite3` as its database, and store secrets in a
root-owned environment file based on
[deploy/litletter.env.example](deploy/litletter.env.example). Then install and
enable the units:

```console
sudo cp deploy/litletter.service deploy/litletter.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now litletter.timer
systemctl list-timers litletter.timer
```

SQLite is the only persistent service. It records papers, category memberships,
run watermarks, immutable rendered editions, and delivery attempts. There is no
database daemon to administer; back up the database file when no run is active.

## Fetching papers

```python
from datetime import date

from litletter.sources import BioRxivClient, PubMedClient, PubMedDateField

with PubMedClient(email="you@example.com") as pubmed:
    papers = pubmed.search(
        '"single cell"[Title/Abstract] AND cancer[Title/Abstract]',
        since=date(2026, 8, 1),
        until=date(2026, 8, 9),
    )

with PubMedClient(
    email="you@example.com",
    date_field=PubMedDateField.PUBLICATION,
) as pubmed:
    papers_published_in_range = pubmed.search(
        "cancer[Title/Abstract]",
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

PubMed date bounds use its Entrez date by default: the date a record entered the
database, which is appropriate for a daily discovery job. Construct a client
with `date_field=PubMedDateField.PUBLICATION` when the window should instead
mean the paper's publication date, as in a retrospective "past month" search.
Publication mode also enforces the range against Litletter's normalized date,
which prefers an electronic publication date over a later journal issue date.

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
- `title:`, `abstract:`, `title_abstract:`, `journal:`, `journal_group:`, and
  `category:` field prefixes.
- Field-scoped groups such as `title:(cancer OR tumour)`.

Unqualified terms search the title and abstract. Unquoted terms match complete
words, while quoted phrases match a contiguous substring. Matching is Unicode
case-insensitive and collapses runs of whitespace. Boolean operators must be
explicit; Litletter does not insert an implicit `AND` between adjacent terms.

### Journals and journal groups

`journal:` performs exact, case-insensitive identity matching rather than a
substring search. For PubMed records it can match the full journal title,
abbreviation, NLM ID, or retained ISSN. A field-scoped group is useful for a
short ad hoc list:

```python
query = parse_query("title_abstract:cancer AND journal:(Nature OR Science OR Cell)")
```

Litletter also ships versioned, sourced collections for publisher families and
the Nature Index:

```python
from litletter import get_journal_catalog, parse_query

catalog = get_journal_catalog()
catalog.names()
catalog.get("nature_portfolio").journals

query = parse_query("title_abstract:cancer AND journal_group:nature_index_current")
```

The built-in canonical group names are `flagship_nsc`, `nature_research`,
`nature_reviews`, `nature_communications`, `nature_progress`, `scientific_series`,
`npj_series`, `nature_portfolio`, `science_family`, `cell_press`, and
`nature_index_2026`. The aliases `nsc`, `nature_family`, `nature_index`, and
`nature_index_current` are also accepted. Each group records its source URL and
snapshot date so a newsletter query does not silently change when a publisher
updates its list.

The Nature Index group represents publication membership only. Nature Index
also applies article-type rules when calculating its metrics; Litletter does
not attempt to reproduce those rules.

`category:` matches bioRxiv's supplied category locally, for example
`category:"systems biology"`. PubMed records do not have that field.

## Discovering matching papers

The discovery helper connects fetching and local matching:

```python
from datetime import date

from litletter import discover_papers, parse_query
from litletter.sources import BioRxivClient, PubMedClient

query = parse_query(
    'title:("spatial transcriptomics" OR single-cell) AND abstract:(cancer OR tumour)'
)

with (
    PubMedClient(email="you@example.com") as pubmed,
    BioRxivClient() as biorxiv,
):
    papers = discover_papers(
        query,
        since=date(2026, 8, 1),
        until=date(2026, 8, 9),
        pubmed=pubmed,
        biorxiv=biorxiv,
    )
```

Litletter compiles a broad positive PubMed selector to reduce downloads, then
applies the original query locally. Negative-only branches are not used for
candidate selection because doing so could exclude valid results. When no safe
positive selector exists, PubMed records are fetched using only the requested
Entrez date range. Candidate limits can be supplied for previews, but they are
applied before local filtering and can therefore reduce the number of matches.
Journal constraints compile to PubMed's `[Journal]` field; large groups are sent
with POST automatically.

## Interactive scratchpad

[scripts/demo.py](scripts/demo.py) is a deliberately simple, block-oriented
scratchpad for exploring Litletter from an existing interactive Python session.
Set your NCBI contact email first:

```console
export LITLETTER_NCBI_EMAIL="you@example.com"
```

Open the script in your editor and send its blocks to IPython. The variables
remain available for inspection, including `query`, `pubmed_candidate_query`,
the source clients, and the final `papers` list. Edit `query_text`, the date
range, and candidate limits directly in the first blocks. An optional NCBI API
key can be supplied through `LITLETTER_NCBI_API_KEY`.

The scratchpad enables `INFO` logging for the `litletter` package, showing the
date window, PubMed selector, source candidate counts, local match counts, and
the final total while discovery runs. Change `logging.INFO` to `logging.DEBUG`
in the first block to additionally see PubMed batches and source pagination.
