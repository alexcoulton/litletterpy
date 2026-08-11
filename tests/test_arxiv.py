from __future__ import annotations

from datetime import date

import httpx
import pytest

from litletter.author_groups import _parse_catalog
from litletter.errors import ResponseParseError
from litletter.models import PaperSource
from litletter.query import compile_arxiv_candidate_query, parse_query
from litletter.sources import ArXivClient

_FEED_TEMPLATE = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>{total}</opensearch:totalResults>
  {entries}
</feed>
"""

_FIRST_ENTRY = """\
<entry>
  <id>http://arxiv.org/abs/2608.12345v2</id>
  <updated>2026-08-09T12:30:00Z</updated>
  <published>2026-08-08T10:00:00Z</published>
  <title>  A cancer model with spatial data  </title>
  <summary> We report\n a result. </summary>
  <author><name>Ada Lovelace</name></author>
  <author><name>Grace Hopper</name></author>
  <category term="q-bio.CB" />
  <category term="stat.ML" />
  <arxiv:primary_category term="q-bio.CB" />
  <arxiv:doi>https://doi.org/10.1000/example</arxiv:doi>
</entry>
"""

_SECOND_ENTRY = """\
<entry>
  <id>http://arxiv.org/abs/hep-th/9901001v1</id>
  <updated>2026-08-07T12:30:00+00:00</updated>
  <published>2026-08-07T12:30:00+00:00</published>
  <title>An older identifier</title>
  <summary>A physics abstract.</summary>
  <author><name>Emmy Noether</name></author>
  <category term="hep-th" />
  <arxiv:primary_category term="hep-th" />
</entry>
"""

_THIRD_ENTRY = """\
<entry>
  <id>https://arxiv.org/abs/2608.00001v3</id>
  <updated>2026-08-06T08:00:00Z</updated>
  <published>2026-08-06T07:00:00Z</published>
  <title>A third paper</title>
  <summary>A final abstract.</summary>
  <author><name>Katherine Johnson</name></author>
  <category term="cs.LG" />
  <arxiv:primary_category term="cs.LG" />
</entry>
"""


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("title:cancer", "ti:cancer"),
        ("abstract:cancer", "abs:cancer"),
        ("title_abstract:cancer", "(ti:cancer OR abs:cancer)"),
        (
            "title_abstract:'single cell'",
            "((ti:single OR abs:single) AND (ti:cell OR abs:cell))",
        ),
        ("category:q-bio.CB", "cat:q-bio.CB"),
        ("category:'computer science'", None),
        ("author:Coulton", None),
        ("author:'Alex Coulton'", "au:coulton"),
        (
            "title:cancer OR author:'Alex Coulton'",
            "(ti:cancer OR au:coulton)",
        ),
        ("title:cancer AND NOT abstract:review", "ti:cancer"),
        ("title:cancer OR journal:Nature", None),
        ("publication_type:original_research", None),
    ],
)
def test_compile_arxiv_candidate_query(query: str, expected: str | None) -> None:
    assert compile_arxiv_candidate_query(query) == expected
    assert compile_arxiv_candidate_query(parse_query(query)) == expected


def test_author_group_compiles_to_arxiv_author_selector() -> None:
    catalog = _parse_catalog(
        {
            "version": 1,
            "groups": {"watchlist": {"authors": ["Alex Coulton", "Jane Smith"]}},
        }
    )
    query = parse_query("author_group:watchlist", author_catalog=catalog)

    assert compile_arxiv_candidate_query(query) == "(au:coulton OR au:smith)"


def test_search_paginates_and_normalizes_atom_entries() -> None:
    pages = {
        0: _FEED_TEMPLATE.format(total=3, entries=_FIRST_ENTRY + _SECOND_ENTRY),
        2: _FEED_TEMPLATE.format(total=3, entries=_THIRD_ENTRY),
    }
    starts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        start = int(params["start"])
        starts.append(start)
        assert params["search_query"] == (
            "(ti:cancer) AND submittedDate:[202608010000 TO 202608092359]"
        )
        assert params["max_results"] == "2"
        assert params["sortBy"] == "submittedDate"
        assert params["sortOrder"] == "descending"
        return httpx.Response(200, text=pages[start])

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http_client,
        ArXivClient(
            page_size=2,
            requests_per_second=0,
            http_client=http_client,
        ) as client,
    ):
        papers = client.search(
            "ti:cancer",
            since=date(2026, 8, 1),
            until=date(2026, 8, 9),
        )

    assert starts == [0, 2]
    assert [paper.source_id for paper in papers] == [
        "2608.12345",
        "hep-th/9901001",
        "2608.00001",
    ]
    first = papers[0]
    assert first.source is PaperSource.ARXIV
    assert first.title == "A cancer model with spatial data"
    assert first.abstract == "We report a result."
    assert [author.name for author in first.authors] == [
        "Ada Lovelace",
        "Grace Hopper",
    ]
    assert first.published_at == date(2026, 8, 8)
    assert first.updated_at == date(2026, 8, 9)
    assert first.doi == "10.1000/example"
    assert first.category == "q-bio.CB, stat.ML"
    assert first.version == 2
    assert first.url == "https://arxiv.org/abs/2608.12345"
    assert first.is_original_research is True
    assert parse_query("category:stat.ML").matches(first)


def test_fetch_uses_only_the_submitted_date_selector() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search_query"] == (
            "submittedDate:[202608080000 TO 202608092359]"
        )
        return httpx.Response(
            200,
            text=_FEED_TEMPLATE.format(total=1, entries=_FIRST_ENTRY),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ArXivClient(requests_per_second=0, http_client=http_client)
        papers = client.fetch(
            since=date(2026, 8, 8),
            until=date(2026, 8, 9),
            max_results=1,
        )

    assert [paper.source_id for paper in papers] == ["2608.12345"]


@pytest.mark.parametrize(
    "body",
    [
        "not xml",
        _FEED_TEMPLATE.format(total="missing", entries=""),
        _FEED_TEMPLATE.format(
            total=1,
            entries="<entry><id>http://arxiv.org/abs/2608.12345v1</id></entry>",
        ),
    ],
)
def test_fetch_rejects_malformed_atom(body: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ArXivClient(requests_per_second=0, http_client=http_client)
        with pytest.raises(ResponseParseError):
            client.fetch(since=date(2026, 8, 1), until=date(2026, 8, 9))


@pytest.mark.parametrize(
    "arguments",
    [
        {"page_size": 0},
        {"page_size": 2001},
    ],
)
def test_client_rejects_invalid_page_size(arguments: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="page_size"):
        ArXivClient(**arguments)
