# Litletter

Litletter sends you a daily email containing newly published papers that match
your interests. It searches PubMed and bioRxiv, groups papers into your chosen
categories, and remembers what it has already sent.

## Install

Litletter requires Python 3.11 or newer. Install it with
[pipx](https://pipx.pypa.io/) and create the starter files in one command:

```console
pipx install litletter && litletter init
```

If you already use [uv](https://docs.astral.sh/uv/), the equivalent is:

```console
uv tool install litletter && litletter init
```

Until Litletter's first PyPI release, clone this repository and replace
`litletter` in the install command with `.`. For example:

```console
pipx install . && litletter init
```

This creates `litletter.json`, a private provider configuration, and a small
SQLite database. SQLite keeps track of delivered papers so they are not sent
twice; there is no database server to install.

## Set up email delivery

Create a free [Resend](https://resend.com/) account and an API key. Find your
private provider configuration with:

```console
litletter app-config path
```

Open that file and add your PubMed contact email and Resend key:

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

Then open `litletter.json` and change the newsletter addresses:

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

Each category in `litletter.json` becomes a section in the email. Give it a
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
    "sources": ["biorxiv"]
  }
]
```

Queries support `AND`, `OR`, `NOT`, parentheses, and quoted phrases. The most
useful search fields are:

- `title:`, `abstract:`, or `title_abstract:`
- `journal:` for one journal
- `journal_group:` for built-in collections such as `flagship_nsc`,
  `nature_portfolio`, `science_family`, `cell_press`, and
  `nature_index_current`
- `publication_type:original_research` to exclude reviews, news, editorials,
  corrections, and other non-research material
- `category:` for a bioRxiv subject category

For example:

```text
title_abstract:(cancer OR tumour) AND NOT title:review
title:"spatial transcriptomics" AND journal_group:nature_portfolio
```

Categories appear in the order listed. A paper matching several categories is
shown only once, under its first match. If you add a bioRxiv category, also set
`sources.biorxiv.enabled` to `true` in the same file.

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

Use absolute paths and keep `litletter.json` and its `state` directory in the
working folder. Litletter prevents two scheduled runs from operating on the
same database simultaneously.

For a server using systemd, ready-to-adapt service and timer files are provided
in [`deploy/`](deploy/). See [DEVELOPMENT.md](DEVELOPMENT.md#server-deployment)
for the setup commands.

## Optional AI summaries

Litletter works without AI summaries. To enable them, put a DeepSeek API key in
the `deepseek-default` entry of your private provider configuration:

```json
"deepseek-default": {
  "type": "deepseek",
  "api_key": "your_deepseek_api_key",
  "base_url": "https://api.deepseek.com",
  "timeout_seconds": 60
}
```

Then enable summarization in `litletter.json`:

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
