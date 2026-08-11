# Litletter

Litletter sends you a daily email containing newly published papers that match
your interests. It searches PubMed, bioRxiv, medRxiv, and arXiv, groups papers
into your chosen categories, and remembers what it has already sent.

## Suggested setup

For reliable automatic delivery, run Litletter on an always-on machine such as
a cheap VPS, a home server, or an institutional compute cluster. A scheduler
runs `litletter run` once each day; Litletter does not need to remain running
between deliveries. It needs only outbound internet access and persistent
storage for its two JSON files and SQLite database.

You can test Litletter on a laptop, but scheduled delivery will happen only
while that laptop is awake. The daily scheduling examples are covered below.

## Install

Litletter requires Python 3.11 or newer. Install it with
[uv](https://docs.astral.sh/uv/) and create the starter files in one command:

```console
uv tool install litletter && litletter init
```

Alternatively, install it with [pipx](https://pipx.pypa.io/):

```console
pipx install litletter && litletter init
```

Until Litletter's first PyPI release, clone this repository and replace
`litletter` in the install command with `.`. For example:

```console
uv tool install . && litletter init
```

Run this command in the folder where you want to keep your newsletter. It
creates three files:

- `./litletter.json` — newsletter addresses, categories, and behavior
- `~/.config/litletter/app.json` — PubMed, Resend, and optional DeepSeek
  credentials
- `./state/litletter.sqlite3` — delivery history and cached papers

SQLite keeps track of delivered papers so they are not sent twice; there is no
database server to install. If `XDG_CONFIG_HOME` is set, `app.json` is created
under `$XDG_CONFIG_HOME/litletter/` instead of `~/.config/litletter/`.

## Set up email delivery

Create a free [Resend](https://resend.com/) account and an API key. Open
`~/.config/litletter/app.json` and add your PubMed contact email and Resend key:

```json
"pubmed-default": {
  "type": "pubmed",
  "email": "you@example.com"
},
"resend-default": {
  "type": "resend",
  "api_key": "re_your_api_key"
}
```

Then open `./litletter.json` and change the newsletter addresses:

```json
"newsletter": {
  "title": "My Litletter",
  "from": "Litletter <onboarding@resend.dev>",
  "to": ["you@example.com"],
  "timezone": "Europe/London",
  "include_abstracts": false
}
```

The Resend onboarding address can send test messages only to the email address
on your Resend account. To send to other people, verify a domain with Resend and
use an address on that domain for `from`.

## Choose your categories

Each category in `./litletter.json` becomes a section in the email. Give it a
unique ID, a heading, a search query, and the sources to search:

```json
"categories": [
  {
    "id": "nsc-cancer",
    "name": "Nature, Science and Cell: Cancer",
    "query": "title_abstract:cancer AND journal_group:flagship_nsc AND publication_type:original_research",
    "sources": ["pubmed"]
  },
  {
    "id": "cancer-preprints",
    "name": "Cancer Preprints",
    "query": "title_abstract:cancer AND publication_type:original_research",
    "sources": ["biorxiv", "medrxiv"]
  },
  {
    "id": "machine-learning-preprints",
    "name": "Machine Learning Preprints",
    "query": "title_abstract:'machine learning' AND category:(cs.LG OR stat.ML)",
    "sources": ["arxiv"]
  }
]
```

Enable each source used by a category in the `sources` section of the same
file. PubMed is enabled in the starter configuration; the preprint sources are
opt-in:

```json
"biorxiv": {"enabled": true},
"medrxiv": {"enabled": true},
"arxiv": {"enabled": true}
```

bioRxiv, medRxiv, and arXiv do not require API keys.

Queries support `AND`, `OR`, `NOT`, parentheses, and phrases in single or double
quotes. The most useful search fields are:

- `title:`, `abstract:`, or `title_abstract:`
- `journal:` for one journal
- `journal_group:` for built-in collections such as `flagship_nsc`,
  `nature_portfolio`, `science_family`, `cell_press`, and
  `nature_index_current`
- `publication_type:original_research` to exclude reviews, news, editorials,
  corrections, and other non-research material
- `category:` for a bioRxiv, medRxiv, or arXiv subject category

For example:

```text
title_abstract:cancer OR title_abstract:tumour
(title_abstract:cancer OR title_abstract:tumour) AND NOT title:review
title:"spatial transcriptomics" AND journal_group:nature_portfolio
```

Use parentheses around the `OR` alternatives when combining them with another
condition. The shorter `title_abstract:(cancer OR tumour)` syntax is equivalent.
Single quotes are convenient for phrases inside JSON because they do not need
escaping:

```json
"query": "title_abstract:'single cell' OR title_abstract:'spatial transcriptomics'"
```

Categories appear in the order listed. A paper matching several categories is
shown only once, under its first match.

## Preview and send

The first run searches the number of days set by `initial_lookback_days`. Review
that initial batch without sending it:

```console
litletter run --bootstrap --dry-run --no-summarization \
  --output litletter-preview.html
```

Open `litletter-preview.html`. If it looks right, send the newsletter:

```console
litletter run --no-summarization
```

After this first run, every ordinary `litletter run` searches only for new
papers, with a small overlap to allow for indexing delays. Successfully sent
papers are automatically excluded from later emails.

Check the current state at any time with:

```console
litletter status
```

## Send it every day

Litletter performs one finite update each time `litletter run` is called, so it
can be scheduled with cron. Find the executable with `which litletter`, then add
a daily entry with `crontab -e`. For example:

```cron
15 7 * * * cd /path/to/your/litletter-folder && /absolute/path/to/litletter run
```

Use absolute paths and keep `./litletter.json` and `./state/litletter.sqlite3`
in the working folder. Litletter prevents two scheduled runs from operating on
the same database simultaneously.

For a server using systemd, ready-to-adapt service and timer files are provided
in [`deploy/`](deploy/). See [DEVELOPMENT.md](DEVELOPMENT.md#server-deployment)
for the setup commands.

## Optional AI summaries

Litletter works without AI summaries. To enable them, put a DeepSeek API key in
the `deepseek-default` entry of `~/.config/litletter/app.json`:

```json
"deepseek-default": {
  "type": "deepseek",
  "api_key": "your_deepseek_api_key",
  "base_url": "https://api.deepseek.com",
  "timeout_seconds": 60
}
```

Then enable summarization in `./litletter.json`:

```json
"summarization": {
  "enabled": true,
  "provider": "deepseek-default",
  "model": "deepseek-v4-flash",
  "max_words": 100,
  "audience": "a scientifically literate reader outside the paper's specialty",
  "failure_policy": "fallback"
}
```

Use `--no-summarization` on any run to skip it temporarily. Summaries are cached
so delivery retries do not call the model again.

## More information

- [DEVELOPMENT.md](DEVELOPMENT.md) covers advanced configuration, query and
  fetching behavior, delivery recovery, the Python API, and development.
- [`examples/litletter.json`](examples/litletter.json) is a complete newsletter
  configuration.
- [`examples/app.json`](examples/app.json) shows all provider profiles.
