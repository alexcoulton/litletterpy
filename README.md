# Litletter

Litletter is a local Python package for fetching and normalizing papers from
PubMed and bioRxiv, then applying a shared Boolean query language to their
titles, abstracts, journals, and categories. Its CLI can run those searches on
a schedule, retain delivery state in SQLite, and send a categorized email
newsletter through Resend or Postmark without resending previously delivered
papers.

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

Litletter deliberately separates machine-level provider credentials from
newsletter behavior. The global app config defaults to
`~/.config/litletter/app.json` and defines reusable PubMed, summarizer, and
mailer profiles. A newsletter config references those profiles and contains
addressing, discovery behavior, ordered searches, and presentation settings.
`LITLETTER_APP_CONFIG` can override the global location.

Create a private global template and edit its email and credentials:

```console
uv run litletter app-config init
uv run litletter app-config path
uv run litletter app-config validate
```

Secrets can be stored directly as `api_key`/`server_token`, or indirectly as
`api_key_env`/`server_token_env`. The generated file uses environment references
and mode `0600`, keeping secrets outside the repository. See
[examples/app.json](examples/app.json) for the complete structure.

Start the newsletter from [examples/litletter.json](examples/litletter.json).
Paths such as `database` are resolved relative to the newsletter JSON file, not
the shell's current directory.

Each category has a stable lowercase ID, a display name, a Litletter Boolean
query, and one or more sources. For example:

```json
{
  "id": "nsc-cancer",
  "name": "Nature, Science and Cell: Cancer",
  "query": "title_abstract:cancer AND journal_group:flagship_nsc AND publication_type:original_research",
  "sources": ["pubmed"]
}
```

Category order controls the email section order. If a paper matches several
categories, it appears once under the first matching category and lists the
others as secondary categories.

Newsletter HTML uses a compact table with date, title, authors, and journal.
Paper titles resolve through `https://doi.org/` when a DOI is available and
fall back to the source record otherwise. Abstracts are hidden by default;
set `newsletter.include_abstracts` to `true` to restore excerpts controlled by
`abstract_max_characters`. Enabled AI summaries remain visible independently.

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

### Optional DeepSeek summaries

When `summarization.enabled` is true, Litletter summarizes each unsent paper that
has an abstract before rendering. The built-in DeepSeek adapter requests
validated JSON containing a short takeaway and a plain-language summary. The
newsletter labels generated text clearly and falls back to the original
abstract when `failure_policy` is `fallback`.

Enabling the feature sends each paper's title and public abstract to the
selected DeepSeek profile. Use `failure_policy: "abort"` when every paper must
be summarized before an edition can be created.

Successful summaries are cached in SQLite using the paper text, provider,
model, and prompt identity. Delivery retries never call the model again.
Changing the model or prompt creates a new cache identity without destroying
older summaries.

The runner depends on a provider-neutral `Summarizer` interface; DeepSeek is an
adapter rather than a dependency of discovery or rendering, so another provider
can be added without changing the pipeline.

Precompute pending summaries without discovering or sending anything:

```console
uv run litletter summarize --config litletter.json --pending
```

Disable summarization persistently with `"enabled": false`, or bypass it for one
run while retaining the cache:

```console
uv run litletter run --config litletter.json --no-summarization
```

Dry runs can create cached summaries and therefore make billable DeepSeek API
calls, but still never create or deliver an email edition. Papers without an
abstract remain unsummarized rather than being guessed from the title.

### Resend delivery

Create a Resend API key and verify a domain that you own. Set
`newsletter.from` to any address at that verified domain, select a Resend mailer
profile, and omit the Postmark-only `delivery.message_stream` setting:

```json
"delivery": {
  "provider": "resend-default"
}
```

Provide the API key through the environment variable referenced by the profile
or store it directly as `api_key` in the private app config:

```console
export LITLETTER_RESEND_API_KEY="re_your-api-key"
uv run litletter run --config litletter.json
```

Litletter sends through Resend's Email API and attaches an idempotency key based
on the immutable edition ID. Resend requires a verified domain for sending to
real recipients; public email domains such as Gmail cannot be verified as your
sending domain.

### Postmark delivery

Create a Postmark server, verify the individual address used by
`newsletter.from`, and create a Broadcast Message Stream whose ID matches
`delivery.message_stream`. Put its server token in the selected mailer profile
or the environment variable referenced by that profile:

```console
export LITLETTER_POSTMARK_TOKEN="your-server-token"
uv run litletter run --config litletter.json
```

A verified individual sender signature is sufficient for this single-user
setup; the sender and recipient can be the same address. A Broadcast stream is
appropriate for newsletters and lets Postmark apply its broadcast/unsubscribe
handling.

To select Postmark instead, use both its provider profile and stream ID:

```json
"delivery": {
  "provider": "postmark-default",
  "message_stream": "broadcasts"
}
```

### Delivery state and recovery

Use `litletter status --config litletter.json` to inspect the watermark, pending
papers, submitted editions, and any edition requiring attention. Provider
requests are deliberately not retried automatically. A definite rejection
leaves a failed edition that can be resent after correction:

```console
uv run litletter run --config litletter.json --retry-open-edition
```

If a timeout makes the outcome uncertain, the edition remains in `sending` and
all future sends stop. Check the selected provider using the edition metadata
shown by `litletter status`, then resolve it explicitly:

```console
# The message exists at the provider:
uv run litletter edition resolve --config litletter.json EDITION_ID \
  --delivered --message-id PROVIDER_MESSAGE_ID

# The provider confirms that it was not accepted:
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
15 7 * * * cd /opt/litletter && /opt/litletter/.venv/bin/litletter run --config /etc/litletter/litletter.json --app-config /etc/litletter/app.json
```

The sample [systemd service](deploy/litletter.service) and
[timer](deploy/litletter.timer) are more resilient: the timer is persistent, so
a run missed while the server was offline starts after it returns. Adapt the
paths, copy the two JSON files to `/etc/litletter/`, use
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
cached summaries and token usage, run watermarks, immutable rendered editions,
and delivery attempts. There is no database daemon to administer; back up the
database file when no run is active. Running `litletter db init` also applies
supported schema migrations to an existing database.

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
- `title:`, `abstract:`, `title_abstract:`, `journal:`, `journal_group:`,
  `category:`, and `publication_type:` field prefixes.
- Field-scoped groups such as `title:(cancer OR tumour)`.

Unqualified terms search the title and abstract. Unquoted terms match complete
words, while quoted phrases match a contiguous substring. Matching is Unicode
case-insensitive and collapses runs of whitespace. Boolean operators must be
explicit; Litletter does not insert an implicit `AND` between adjacent terms.

Use `publication_type:original_research` to retain research articles and
bioRxiv preprints while excluding PubMed reviews, systematic reviews,
meta-analyses, news, editorials, comments, letters, corrections, retractions,
guidelines, and other non-research formats. PubMed filtering uses its controlled
publication-type metadata rather than title keywords. Exact PubMed types are
also searchable, for example `publication_type:"Randomized Controlled Trial"`.

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
