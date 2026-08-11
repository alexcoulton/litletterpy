from __future__ import annotations

from datetime import date

import httpx
import pytest

from litletter.errors import ResponseParseError
from litletter.models import PaperSource
from litletter.sources import MedRxivClient


def test_fetch_uses_medrxiv_feed_and_normalizes_records() -> None:
    payload = {
        "messages": [{"total": "1", "count": 1, "cursor": 0}],
        "collection": [
            {
                "doi": "10.1101/2026.08.08.123456",
                "title": "A clinical cancer study",
                "authors": "Ada Lovelace; Grace Hopper",
                "date": "2026-08-08",
                "version": "2",
                "category": "oncology",
                "abstract": "We report a clinical result.",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == ("/details/medrxiv/2026-08-01/2026-08-09/0/json")
        return httpx.Response(200, json=payload)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http_client,
        MedRxivClient(requests_per_second=0, http_client=http_client) as client,
    ):
        papers = client.fetch(since=date(2026, 8, 1), until=date(2026, 8, 9))

    assert len(papers) == 1
    paper = papers[0]
    assert paper.source is PaperSource.MEDRXIV
    assert paper.source_id == "10.1101/2026.08.08.123456"
    assert paper.title == "A clinical cancer study"
    assert [author.name for author in paper.authors] == [
        "Ada Lovelace",
        "Grace Hopper",
    ]
    assert paper.published_at == date(2026, 8, 8)
    assert paper.category == "oncology"
    assert paper.version == 2
    assert paper.url == ("https://www.medrxiv.org/content/10.1101/2026.08.08.123456v2")
    assert paper.is_original_research is True


def test_fetch_reports_medrxiv_parse_errors() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = MedRxivClient(requests_per_second=0, http_client=http_client)
        with pytest.raises(ResponseParseError, match="medRxiv"):
            client.fetch(since=date(2026, 8, 1), until=date(2026, 8, 9))
