from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from litletter.errors import ResponseParseError
from litletter.models import PaperSource
from litletter.sources import BioRxivClient


def test_fetch_paginates_and_normalizes_records(fixture_dir: Path) -> None:
    pages = {
        0: json.loads((fixture_dir / "biorxiv" / "page-0.json").read_text()),
        2: json.loads((fixture_dir / "biorxiv" / "page-2.json").read_text()),
    }
    cursors: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == "litletter/0.1"
        assert request.url.path.startswith("/details/biorxiv/")
        cursor = int(request.url.path.rstrip("/").rsplit("/", 2)[-2])
        cursors.append(cursor)
        return httpx.Response(200, json=pages[cursor])

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http_client,
        BioRxivClient(
            requests_per_second=0,
            http_client=http_client,
        ) as client,
    ):
        papers = client.fetch(
            since=date(2026, 8, 1),
            until=date(2026, 8, 9),
        )

    assert cursors == [0, 2]
    assert [paper.source_id for paper in papers] == [
        "10.1101/2026.08.08.123456",
        "10.1101/2026.08.07.654321",
        "10.1101/2026.08.06.111111",
    ]
    first = papers[0]
    assert first.source is PaperSource.BIORXIV
    assert first.title == "A spatial atlas of the liver"
    assert first.abstract == "We mapped many cells."
    assert [author.name for author in first.authors] == [
        "Ada Lovelace",
        "Grace Hopper",
    ]
    assert first.published_at == date(2026, 8, 8)
    assert first.category == "bioinformatics"
    assert first.version == 1
    assert first.url.endswith("10.1101/2026.08.08.123456v1")
    assert papers[-1].abstract is None


def test_fetch_stops_at_max_results(fixture_dir: Path) -> None:
    first_page = json.loads((fixture_dir / "biorxiv" / "page-0.json").read_text())
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=first_page)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BioRxivClient(requests_per_second=0, http_client=http_client)
        papers = client.fetch(
            since=date(2026, 8, 1),
            until=date(2026, 8, 9),
            max_results=1,
        )

    assert len(papers) == 1
    assert calls == 1


def test_fetch_retries_transient_response(fixture_dir: Path) -> None:
    page = json.loads((fixture_dir / "biorxiv" / "page-2.json").read_text())
    page["messages"][0]["total"] = "1"
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, json=page)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BioRxivClient(
            max_retries=1,
            requests_per_second=0,
            http_client=http_client,
        )
        papers = client.fetch(since=date(2026, 8, 6), until=date(2026, 8, 6))

    assert attempts == 2
    assert len(papers) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": True},
        {"messages": [{"total": "1"}], "collection": [{"title": "No DOI"}]},
    ],
)
def test_fetch_rejects_malformed_payload(payload: dict[str, object]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = BioRxivClient(requests_per_second=0, http_client=http_client)
        with pytest.raises(ResponseParseError):
            client.fetch(since=date(2026, 8, 1), until=date(2026, 8, 9))
