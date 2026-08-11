"""arXiv API client."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Final
from urllib.parse import urlsplit

import httpx

from litletter.errors import ResponseParseError
from litletter.models import Author, Paper, PaperSource
from litletter.sources._http import HttpRequester

_LOGGER = logging.getLogger(__name__)

_QUERY_URL = "https://export.arxiv.org/api/query"
_ATOM: Final = "http://www.w3.org/2005/Atom"
_ARXIV: Final = "http://arxiv.org/schemas/atom"
_OPENSEARCH: Final = "http://a9.com/-/spec/opensearch/1.1/"
_NAMESPACES = {"atom": _ATOM, "arxiv": _ARXIV, "opensearch": _OPENSEARCH}
_VERSION_SUFFIX = re.compile(r"v(?P<version>[1-9][0-9]*)$")


class ArXivClient:
    """Search and normalize arXiv papers from a submitted-date interval."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        requests_per_second: float = 1 / 3,
        page_size: int = 1000,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not 1 <= page_size <= 2000:
            raise ValueError("page_size must be between 1 and 2000")
        self._page_size = page_size
        self._http = HttpRequester(
            source="arXiv",
            user_agent="litletter/0.1",
            timeout=timeout,
            max_retries=max_retries,
            requests_per_second=requests_per_second,
            client=http_client,
        )

    def __enter__(self) -> ArXivClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close network resources owned by this client."""
        self._http.close()

    def search(
        self,
        query: str,
        *,
        since: date,
        until: date,
        max_results: int | None = None,
    ) -> list[Paper]:
        """Return arXiv records matching a native selector and date interval."""
        if not query.strip():
            raise ValueError("query must not be empty")
        return self._fetch(
            selector=query.strip(),
            since=since,
            until=until,
            max_results=max_results,
        )

    def fetch(
        self,
        *,
        since: date,
        until: date,
        max_results: int | None = None,
    ) -> list[Paper]:
        """Return arXiv records submitted within an inclusive date interval."""
        return self._fetch(
            selector=None,
            since=since,
            until=until,
            max_results=max_results,
        )

    def _fetch(
        self,
        *,
        selector: str | None,
        since: date,
        until: date,
        max_results: int | None,
    ) -> list[Paper]:
        if since > until:
            raise ValueError("since must not be after until")
        if max_results is not None and max_results < 0:
            raise ValueError("max_results must not be negative")
        if max_results == 0:
            return []

        date_selector = f"submittedDate:[{since:%Y%m%d}0000 TO {until:%Y%m%d}2359]"
        search_query = (
            f"({selector}) AND {date_selector}" if selector else date_selector
        )
        start = 0
        total: int | None = None
        papers: list[Paper] = []
        while total is None or start < total:
            remaining = None if max_results is None else max_results - len(papers)
            page_size = (
                self._page_size
                if remaining is None
                else min(self._page_size, remaining)
            )
            if page_size == 0:
                break
            _LOGGER.debug("Requesting arXiv page at offset %d", start)
            response = self._http.request(
                "GET",
                _QUERY_URL,
                params={
                    "search_query": search_query,
                    "start": start,
                    "max_results": page_size,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                },
            )
            page, page_total = _parse_arxiv_response(response)
            _LOGGER.debug(
                "arXiv returned %d records at offset %d of %d total",
                len(page),
                start,
                page_total,
            )
            if total is None:
                total = page_total
            if not page:
                break
            papers.extend(page)
            start += len(page)
            if max_results is not None and len(papers) >= max_results:
                break

        return sorted(
            papers,
            key=lambda paper: (paper.published_at or date.min, paper.source_id),
            reverse=True,
        )


def _parse_arxiv_response(response: httpx.Response) -> tuple[list[Paper], int]:
    try:
        root = ET.fromstring(response.content)
        total_text = root.findtext("opensearch:totalResults", namespaces=_NAMESPACES)
        if total_text is None:
            raise ValueError("missing totalResults")
        total = int(total_text)
        if total < 0:
            raise ValueError("negative totalResults")
        papers = [
            _parse_arxiv_entry(entry)
            for entry in root.findall("atom:entry", _NAMESPACES)
        ]
    except (ET.ParseError, TypeError, ValueError) as exc:
        raise ResponseParseError("arXiv returned malformed Atom XML") from exc
    return papers, total


def _parse_arxiv_entry(entry: ET.Element) -> Paper:
    try:
        entry_id = _required_text(entry, "atom:id")
        source_id, version = _parse_source_id(entry_id)
        title = _required_text(entry, "atom:title")
        abstract = _optional_text(entry, "atom:summary")
        published_at = _parse_datetime(_required_text(entry, "atom:published"))
        updated_at = _parse_datetime(_required_text(entry, "atom:updated"))
        authors = tuple(
            Author(_required_text(author, "atom:name"))
            for author in entry.findall("atom:author", _NAMESPACES)
        )
        doi = _normalize_doi(_optional_text(entry, "arxiv:doi"))
        categories = _categories(entry)
    except (TypeError, ValueError) as exc:
        raise ResponseParseError("arXiv returned an invalid paper entry") from exc

    return Paper(
        source=PaperSource.ARXIV,
        source_id=source_id,
        title=title,
        abstract=abstract,
        authors=authors,
        published_at=published_at,
        updated_at=updated_at,
        doi=doi,
        url=f"https://arxiv.org/abs/{source_id}",
        category=", ".join(categories) or None,
        version=version,
        publication_types=("Preprint",),
    )


def _parse_source_id(value: str) -> tuple[str, int | None]:
    path = urlsplit(value).path
    marker = "/abs/"
    if marker not in path:
        raise ValueError("entry id is not an arXiv abstract URL")
    source_id = path.split(marker, 1)[1].strip("/")
    if not source_id:
        raise ValueError("entry id has no arXiv identifier")
    match = _VERSION_SUFFIX.search(source_id)
    if match is None:
        return source_id, None
    return source_id[: match.start()], int(match.group("version"))


def _parse_datetime(value: str) -> date:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).date()


def _categories(entry: ET.Element) -> tuple[str, ...]:
    primary = entry.find("arxiv:primary_category", _NAMESPACES)
    primary_value = primary.get("term", "").strip() if primary is not None else ""
    values = [primary_value] if primary_value else []
    for category in entry.findall("atom:category", _NAMESPACES):
        value = category.get("term", "").strip()
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _required_text(element: ET.Element, path: str) -> str:
    value = _optional_text(element, path)
    if value is None:
        raise ValueError(f"missing {path}")
    return value


def _optional_text(element: ET.Element, path: str) -> str | None:
    value = element.findtext(path, namespaces=_NAMESPACES)
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _normalize_doi(value: str | None) -> str | None:
    if value is None:
        return None
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.casefold().startswith(prefix):
            return value[len(prefix) :]
    return value
