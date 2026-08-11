# Litletter development and operations

This guide contains implementation details, advanced operation, and contributor
workflows. For the normal installation and newsletter setup, start with the
[main README](README.md).

## Configuration model

Litletter separates machine-level provider credentials from newsletter
behavior. The global app config defaults to `~/.config/litletter/app.json` and
defines reusable PubMed, summarizer, and mailer profiles. A newsletter config
references those profiles and contains addressing, discovery behavior, ordered
searches, and presentation settings. `LITLETTER_APP_CONFIG` can override the
global location.

Inspect or validate the app config with:

```console
litletter app-config path
litletter app-config validate
```

Secrets can be stored directly as `api_key`/`server_token`, or indirectly as
`api_key_env`/`server_token_env`. The generated app config uses environment
references and mode `0600`, keeping secrets outside the repository. See
[`examples/app.json`](examples/app.json) for the complete structure.

Start a newsletter from [`examples/litletter.json`](examples/litletter.json).
Paths such as `database` are resolved relative to the newsletter JSON file, not
the shell's current directory.

Newsletter HTML uses a compact table with date, title, authors, and journal.
Titles resolve through `https://doi.org/` when a DOI exists and fall back to the
source record otherwise. Abstracts are hidden by default; set
`newsletter.include_abstracts` to `true` to restore excerpts controlled by
`abstract_max_characters`. Enabled AI summaries remain visible independently.

Validate a newsletter or initialize a database manually when not using
`litletter init`:

```console
litletter config validate --config litletter.json
litletter db init --config litletter.json
```

## Discovery and durable state

The first date window must be approved explicitly. A preview is the safest
bootstrap workflow:

```console
litletter run --config litletter.json \
  --bootstrap --dry-run --output /tmp/litletter-preview.html
```

This fetches and stores matches and advances the discovery watermark, but does
not create a delivery edition or mark any paper sent. Subsequent runs use an
overlapping date window so delayed indexing does not create gaps. Global
deduplication uses successfully submitted editions, so date overlap and papers
matching multiple categories do not cause repeated email entries.

SQLite is the only persistent service. It records papers, category memberships,
cached summaries and token usage, run watermarks, immutable rendered editions,
and delivery attempts. There is no database daemon to administer. Back up the
database while no run is active. `litletter db init` also applies supported
schema migrations to an existing database.

## Summarization details

When `summarization.enabled` is true, Litletter summarizes each unsent paper
that has an abstract before rendering. The built-in DeepSeek adapter requests
validated JSON containing a short takeaway and a plain-language summary. The
newsletter identifies generated text and falls back to the original abstract
when `failure_policy` is `fallback`. Use `failure_policy: "abort"` when every
paper must be summarized before an edition can be created.

Enabling this feature sends each paper's title and public abstract to the
selected DeepSeek profile. Successful summaries are cached using the paper
text, provider, model, and prompt identity. Changing the model or prompt creates
a new cache identity without destroying older summaries.

The runner accepts a provider-neutral `Summarizer` interface. DeepSeek is an
adapter rather than a dependency of discovery or rendering, so another provider
can be added without changing the pipeline.

Precompute pending summaries without discovering or sending:

```console
litletter summarize --config litletter.json --pending
```

Dry runs may create cached summaries and therefore make billable API calls, but
never create or deliver an email edition. Papers without an abstract remain
unsummarized rather than being guessed from the title.

## Email providers

### Resend

Select a Resend provider and omit the Postmark-only message stream:

```json
"delivery": {
  "provider": "resend-default"
}
```

An API key can be stored directly in the private app config or supplied through
the environment variable referenced by `api_key_env`:

```console
export LITLETTER_RESEND_API_KEY="re_your-api-key"
litletter run --config litletter.json
```

Litletter sends through Resend's Email API with an idempotency key based on the
immutable edition ID.

### Postmark

Create a Postmark server, verify the address used by `newsletter.from`, and
create a Broadcast Message Stream. Select the Postmark provider and stream:

```json
"delivery": {
  "provider": "postmark-default",
  "message_stream": "broadcasts"
}
```

Put the server token directly in the app config or use its configured
environment variable:

```console
export LITLETTER_POSTMARK_TOKEN="your-server-token"
litletter run --config litletter.json
```

## Delivery recovery

Use `litletter status --config litletter.json` to inspect the watermark, pending
papers, submitted editions, and editions requiring attention. Provider requests
are deliberately not retried automatically. A definite rejection leaves a
failed edition that can be retried after correcting the problem:

```console
litletter run --config litletter.json --retry-open-edition
```

If a timeout makes the outcome uncertain, the edition remains in `sending` and
future sends stop. Check the provider using the edition metadata shown by
`litletter status`, then resolve it explicitly.

If the message exists at the provider:

```console
litletter edition resolve --config litletter.json EDITION_ID \
  --delivered --message-id PROVIDER_MESSAGE_ID
```

If the provider confirms that it was not accepted:

```console
litletter edition resolve --config litletter.json EDITION_ID --not-delivered
litletter run --config litletter.json --retry-open-edition
```

## Server deployment

One CLI invocation performs one finite run. It takes a non-blocking file lock
next to the database, so overlapping cron or timer invocations exit instead of
running concurrently.

The sample [`litletter.service`](deploy/litletter.service) and
[`litletter.timer`](deploy/litletter.timer) use a persistent systemd timer, so a
run missed while the server was offline starts after it returns. Adapt their
paths, copy the three JSON files to `/etc/litletter/`, use
`/var/lib/litletter/litletter.sqlite3` as the database, and store secrets in a
root-owned environment file based on
[`litletter.env.example`](deploy/litletter.env.example). Then install and enable
the units:

```console
sudo cp deploy/litletter.service deploy/litletter.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now litletter.timer
systemctl list-timers litletter.timer
```

## Fetching papers from Python

```python
from datetime import date

from litletter.sources import (
    ArXivClient,
    BioRxivClient,
    MedRxivClient,
    PubMedClient,
    PubMedDateField,
)

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

with MedRxivClient() as medrxiv:
    medical_preprints = medrxiv.fetch(
        since=date(2026, 8, 1),
        until=date(2026, 8, 9),
    )

with ArXivClient() as arxiv:
    arxiv_preprints = arxiv.search(
        "(ti:cancer OR abs:cancer)",
        since=date(2026, 8, 1),
        until=date(2026, 8, 9),
    )
```

PubMed accepts native PubMed queries. The official bioRxiv/medRxiv API lists
records by date rather than performing Boolean title/abstract searches, so
Litletter evaluates its normalized records locally. Both Rxiv clients use the
same API and pagination implementation with different server identifiers.

The arXiv API accepts native selectors and returns Atom XML. `ArXivClient`
combines its selector with an inclusive `submittedDate` range, normalizes the
latest returned version, and waits three seconds between paginated requests by
default in accordance with arXiv guidance.

PubMed bounds use the Entrez date by default: when a record entered the
database. This suits a daily discovery job. Use
`date_field=PubMedDateField.PUBLICATION` when the window should mean publication
date, such as a retrospective past-month search. Publication mode also enforces
the range against Litletter's normalized date, which prefers an electronic
publication date over a later journal issue date.

## Query language

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

The language supports:

- Case-insensitive `AND`, `OR`, and unary `NOT`.
- Parentheses, with precedence `NOT`, then `AND`, then `OR`.
- Phrases in single or double quotes. Double-quoted phrases accept `\"` and
  `\\` escapes; single-quoted phrases accept `\'` and `\\` escapes.
- `title:`, `abstract:`, `title_abstract:`, `author:`, `author_group:`, `journal:`,
  `journal_group:`, `category:`, and `publication_type:` field prefixes.
- Field-scoped groups such as `title:(cancer OR tumour)`.

Unqualified terms search title and abstract. Unquoted terms match complete
words, while quoted phrases match a contiguous substring. Apostrophes within
unquoted terms, such as `Alzheimer's`, remain part of the term. Matching is
Unicode case-insensitive and collapses whitespace. Boolean operators must be
explicit; Litletter does not insert an implicit `AND` between adjacent terms.

`author:` searches normalized author names and available ORCIDs. Quoted full
names additionally tolerate omitted middle initials and comma-inverted source
formats, so `author:'Alex Coulton'` can match `Alex B. Coulton` or
`Coulton, A.`. For candidate selection, Litletter sends only the likely family
name to PubMed or arXiv and confirms the complete author locally. Ambiguous
single-word author terms are evaluated locally from date-bounded candidates.
When a source supplies only a given-name initial, people sharing the same
family name and initial cannot be distinguished; use an ORCID in the
`author:` query when that source provides one.

Long author watchlists belong in the user-owned `author_groups.json` referenced
by the newsletter config. Version 1 string entries remain supported; version 2
also accepts identity objects with `name`, `orcid`, `aliases`, `institution`,
and `match_initials`. The canonical name is compiled upstream, while all
identity fields except the documentation-only institution contribute to local
matching. Version 2 defaults `match_initials` to false to avoid surname/initial
collisions in large watchlists; version 1 strings retain the original
high-recall behavior. Groups may set a shared `match_initials` default, with an
individual identity overriding it.
A group may compose other groups with `includes`. The query
`author_group:watchlist` expands the named collection into ordinary quoted
`author:` terms before upstream candidate compilation and local evaluation.
This keeps matching semantics identical across PubMed, bioRxiv, medRxiv, and
arXiv. A fingerprint of each referenced collection is persisted with the
category query so changing a list clears stale unsent memberships.

`publication_type:original_research` retains research articles and bioRxiv
preprints while excluding PubMed reviews, systematic reviews, meta-analyses,
news, editorials, comments, letters, corrections, retractions, guidelines, and
other non-research formats. PubMed filtering uses controlled publication-type
metadata rather than title keywords. Exact types can also be searched, for
example `publication_type:"Randomized Controlled Trial"`.

### Journals and journal groups

`journal:` performs exact, case-insensitive identity matching rather than a
substring search. PubMed records can match the full title, abbreviation, NLM
ID, or retained ISSN. Use a field-scoped group for a short ad hoc list:

```python
query = parse_query("title_abstract:cancer AND journal:(Nature OR Science OR Cell)")
```

Litletter ships versioned, sourced collections for publisher families and the
Nature Index:

```python
from litletter import get_journal_catalog, parse_query

catalog = get_journal_catalog()
catalog.names()
catalog.get("nature_portfolio").journals

query = parse_query("title_abstract:cancer AND journal_group:nature_index_current")
```

Canonical groups are `flagship_nsc`, `nature_research`, `nature_reviews`,
`nature_communications`, `nature_progress`, `scientific_series`, `npj_series`,
`nature_portfolio`, `science_family`, `cell_press`, and `nature_index_2026`.
Aliases `nsc`, `nature_family`, `nature_index`, and `nature_index_current` are
also accepted. Each group records its source URL and snapshot date so a query
does not silently change when a publisher updates its list.

The Nature Index group represents publication membership only. Nature Index
also applies article-type rules when calculating its metrics; Litletter does
not reproduce those rules. `category:` matches bioRxiv's supplied category
locally, for example `category:"systems biology"`; medRxiv uses medical subject
categories, while arXiv uses identifiers such as `cs.LG` and `q-bio.CB`.
PubMed records have no such field.

## Discovery implementation

The high-level helper connects fetching and local matching:

```python
from datetime import date

from litletter import discover_papers, parse_query
from litletter.sources import ArXivClient, BioRxivClient, MedRxivClient, PubMedClient

query = parse_query(
    'title:("spatial transcriptomics" OR single-cell) AND abstract:(cancer OR tumour)'
)

with (
    PubMedClient(email="you@example.com") as pubmed,
    BioRxivClient() as biorxiv,
    MedRxivClient() as medrxiv,
    ArXivClient() as arxiv,
):
    papers = discover_papers(
        query,
        since=date(2026, 8, 1),
        until=date(2026, 8, 9),
        pubmed=pubmed,
        biorxiv=biorxiv,
        medrxiv=medrxiv,
        arxiv=arxiv,
    )
```

Litletter compiles broad positive PubMed and arXiv selectors to reduce
downloads, then applies the complete original query locally. Negative-only or
source-irrelevant branches are not used for candidate selection because that
could exclude valid results. If no safe positive selector exists, records are
fetched using only the source's requested date range. Candidate limits are
applied before local filtering and can therefore reduce match counts. PubMed
journal and author constraints compile to its `[Journal]` and `[Author]`
fields; large groups are sent with POST automatically. arXiv author constraints
compile to `au:` selectors. Rxiv records are fetched once per recurring run and
shared across categories before local filtering.

## Interactive scratchpad

[`scripts/demo.py`](scripts/demo.py) is a simple, block-oriented scratchpad for
an existing interactive Python session. Set your NCBI contact email:

```console
export LITLETTER_NCBI_EMAIL="you@example.com"
```

Open the script in an editor and send its blocks to IPython. Variables remain
available for inspection, including `query`, `pubmed_candidate_query`, source
clients, and the final `papers` list. Edit `query_text`, the date range, and
candidate limits in the first blocks. `LITLETTER_NCBI_API_KEY` is optional.

The scratchpad enables `INFO` logs showing the date window, PubMed selector,
source candidate counts, local matches, and final total. Change `logging.INFO`
to `logging.DEBUG` to also see PubMed batches and source pagination.

## Development

Contributors can create the pinned Python 3.12 environment and run all checks
with [uv](https://docs.astral.sh/uv/):

```console
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

The editable package and `litletter` command are installed into `.venv` by
`uv sync`. Activate that environment or prefix development commands with
`uv run`.
