from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from litletter.errors import (
    ApiResponseError,
    PubMedResultLimitError,
    ResponseParseError,
)
from litletter.models import PaperSource
from litletter.sources import PubMedClient


def test_search_paginates_and_normalizes_records(fixture_dir: Path) -> None:
    efetch_xml = (fixture_dir / "pubmed" / "efetch.xml").read_text()
    search_terms: list[str] = []
    search_offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"].startswith("litletter/0.1")
        if request.url.path.endswith("/esearch.fcgi"):
            params = request.url.params
            offset = int(params["retstart"])
            search_offsets.append(offset)
            search_terms.append(params["term"])
            identifier = "111" if offset == 0 else "222"
            return httpx.Response(
                200,
                json={
                    "esearchresult": {
                        "count": "2",
                        "retmax": "1",
                        "retstart": str(offset),
                        "idlist": [identifier],
                    }
                },
            )
        if request.url.path.endswith("/efetch.fcgi"):
            form = parse_qs(request.content.decode())
            assert form["id"] == ["111,222"]
            assert form["email"] == ["reader@example.com"]
            return httpx.Response(200, text=efetch_xml)
        raise AssertionError(f"unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    with (
        httpx.Client(transport=transport) as http_client,
        PubMedClient(
            email="reader@example.com",
            search_page_size=1,
            requests_per_second=0,
            http_client=http_client,
        ) as client,
    ):
        papers = client.search(
            '"single cell"[Title/Abstract]',
            since=date(2026, 8, 1),
            until=date(2026, 8, 9),
        )

    assert search_offsets == [0, 1]
    assert all(
        term.endswith("AND (2026/08/01:2026/08/09[EDAT])") for term in search_terms
    )
    assert [paper.source_id for paper in papers] == ["111", "222"]

    first = papers[0]
    assert first.source is PaperSource.PUBMED
    assert first.title == "A single-cell atlas of useful tissue"
    assert first.abstract == (
        "BACKGROUND: Cells are complicated.\nRESULTS: We found three populations."
    )
    assert [author.name for author in first.authors] == [
        "Ada Lovelace",
        "The Atlas Consortium",
    ]
    assert first.authors[0].orcid == "0000-0001-2345-6789"
    assert first.published_at == date(2026, 8, 8)
    assert first.updated_at == date(2026, 8, 9)
    assert first.doi == "10.1000/useful.111"
    assert first.journal == "Journal of Useful Results"
    assert first.url == "https://pubmed.ncbi.nlm.nih.gov/111/"

    second = papers[1]
    assert second.published_at == date(2026, 7, 1)
    assert second.abstract is None
    assert second.authors[0].name == "GH Hopper Jr"


def test_search_honors_zero_max_results_without_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = PubMedClient(
            email="reader@example.com",
            requests_per_second=0,
            http_client=http_client,
        )
        assert client.search("cancer", max_results=0) == []


def test_search_rejects_more_than_esearch_can_expose() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"esearchresult": {"count": "10001", "idlist": ["1"]}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = PubMedClient(
            email="reader@example.com",
            requests_per_second=0,
            http_client=http_client,
        )
        with pytest.raises(PubMedResultLimitError, match="10,000"):
            client.search("cancer")


def test_search_rejects_malformed_json() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = PubMedClient(
            email="reader@example.com",
            requests_per_second=0,
            http_client=http_client,
        )
        with pytest.raises(ResponseParseError, match="ESearch JSON"):
            client.search("cancer")


def test_search_surfaces_non_retryable_http_errors() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid query")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = PubMedClient(
            email="reader@example.com",
            requests_per_second=0,
            http_client=http_client,
        )
        with pytest.raises(ApiResponseError) as error:
            client.search("cancer")

    assert error.value.status_code == 400
    assert error.value.source == "PubMed"
