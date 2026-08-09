"""Small blocks to send to an interactive Python session."""

# Imports and editable inputs

import logging
import os
from datetime import date, timedelta

from litletter import discover_papers, get_journal_catalog, parse_query
from litletter.query import compile_pubmed_candidate_query
from litletter.sources import PubMedClient, PubMedDateField

logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("litletter").setLevel(logging.INFO)

query_text = """
title_abstract:cancer
AND journal_group:flagship_nsc
""".strip()

until = date.today()
since = until - timedelta(days=30)
ncbi_email = os.environ["LITLETTER_NCBI_EMAIL"]
ncbi_api_key = os.environ.get("LITLETTER_NCBI_API_KEY")


# Inspect the bundled journal collections and one reproducible snapshot

catalog = get_journal_catalog()
catalog.names()

flagship_journals = catalog.get("flagship_nsc")
flagship_journals


# Parse the Litletter query and inspect its AST and PubMed candidate selector

query = parse_query(query_text)
pubmed_candidate_query = compile_pubmed_candidate_query(query)

(query.root, pubmed_candidate_query)


# Create the source clients

pubmed = PubMedClient(
    email=ncbi_email,
    api_key=ncbi_api_key,
    date_field=PubMedDateField.PUBLICATION,
)


# Fetch PubMed candidates and apply the exact local query

papers = discover_papers(
    query,
    since=since,
    until=until,
    pubmed=pubmed,
)

len(papers)


# Examine a compact view of every result

[
    (
        paper.source.value,
        paper.published_at,
        paper.journal,
        paper.title,
        paper.doi,
    )
    for paper in papers
]


# Examine one normalized Paper in detail

paper = papers[0] if papers else None
paper

(
    paper.title if paper else None,
    paper.abstract if paper else None,
    paper.authors if paper else None,
)


# Close the clients when finished

pubmed.close()
