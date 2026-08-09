"""Small blocks to send to an interactive Python session."""

# Imports and editable inputs

import logging
import os
from datetime import date, timedelta

from litletter import discover_papers, parse_query
from litletter.query import compile_pubmed_candidate_query
from litletter.sources import BioRxivClient, PubMedClient

logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("litletter").setLevel(logging.INFO)

query_text = """
title_abstract:("spatial transcriptomics" OR single-cell)
AND NOT title:review
""".strip()

until = date.today()
since = until - timedelta(days=7)
ncbi_email = os.environ["LITLETTER_NCBI_EMAIL"]
ncbi_api_key = os.environ.get("LITLETTER_NCBI_API_KEY")


# Parse the Litletter query and inspect its AST and PubMed candidate selector

query = parse_query(query_text)
pubmed_candidate_query = compile_pubmed_candidate_query(query)

(query.root, pubmed_candidate_query)


# Create the source clients

pubmed = PubMedClient(email=ncbi_email, api_key=ncbi_api_key)
biorxiv = BioRxivClient()


# Fetch candidates from both APIs and apply the local query

papers = discover_papers(
    query,
    since=since,
    until=until,
    pubmed=pubmed,
    biorxiv=biorxiv,
    max_pubmed_candidates=100,
    max_biorxiv_candidates=100,
)

len(papers)


# Examine a compact view of the first results

[
    (
        paper.source.value,
        paper.published_at,
        paper.title,
        paper.doi,
    )
    for paper in papers[:10]
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
biorxiv.close()
