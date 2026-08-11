"""bioRxiv and medRxiv API clients."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from litletter.errors import ResponseParseError
from litletter.models import Author, Paper, PaperSource
from litletter.sources._http import HttpRequester

_LOGGER = logging.getLogger(__name__)

_DETAIL_URL = "https://api.biorxiv.org/details"


class _RxivClient:
    """Fetch and normalize one Rxiv server's papers from a date interval."""

    _server: str
    _source: PaperSource
    _display_name: str
    _content_url: str

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_retries: int = 3,
        requests_per_second: float = 1.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._http = HttpRequester(
            source=self._display_name,
            user_agent="litletter/0.1",
            timeout=timeout,
            max_retries=max_retries,
            requests_per_second=requests_per_second,
            client=http_client,
        )

    def __enter__(self) -> _RxivClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close network resources owned by this client."""
        self._http.close()

    def fetch(
        self,
        *,
        since: date,
        until: date,
        max_results: int | None = None,
    ) -> list[Paper]:
        """Return records posted within an inclusive date interval."""
        if since > until:
            raise ValueError("since must not be after until")
        if max_results is not None and max_results < 0:
            raise ValueError("max_results must not be negative")
        if max_results == 0:
            return []

        cursor = 0
        total: int | None = None
        papers: list[Paper] = []
        while total is None or cursor < total:
            _LOGGER.debug("Requesting %s page at cursor %d", self._display_name, cursor)
            url = (
                f"{_DETAIL_URL}/{self._server}/{since.isoformat()}/"
                f"{until.isoformat()}/{cursor}/json"
            )
            response = self._http.request("GET", url)
            page, page_total = _parse_rxiv_response(
                response, source_name=self._display_name
            )
            _LOGGER.debug(
                "%s returned %d records at cursor %d of %d total",
                self._display_name,
                len(page),
                cursor,
                page_total,
            )
            if total is None:
                total = page_total
            if not page:
                break

            remaining = None if max_results is None else max_results - len(papers)
            selected = page if remaining is None else page[:remaining]
            papers.extend(self._parse_record(record) for record in selected)
            cursor += len(page)
            if max_results is not None and len(papers) >= max_results:
                break

        return sorted(
            papers,
            key=lambda paper: (paper.published_at or date.min, paper.source_id),
            reverse=True,
        )

    def _parse_record(self, record: dict[str, Any]) -> Paper:
        try:
            doi = _required_string(record, "doi")
            title = _required_string(record, "title")
            posted_at = date.fromisoformat(_required_string(record, "date"))
            version_value = record.get("version")
            version = int(version_value) if version_value not in (None, "") else None
            if version is not None and version < 1:
                raise ValueError("version must be positive")
        except (TypeError, ValueError) as exc:
            raise ResponseParseError(
                f"{self._display_name} returned an invalid paper record"
            ) from exc

        abstract = _optional_string(
            record.get("abstract"), source_name=self._display_name
        )
        category = _optional_string(
            record.get("category"), source_name=self._display_name
        )
        authors = _parse_authors(record.get("authors"), source_name=self._display_name)
        version_suffix = f"v{version}" if version is not None else ""
        return Paper(
            source=self._source,
            source_id=doi,
            title=title,
            abstract=abstract,
            authors=authors,
            published_at=posted_at,
            updated_at=None,
            doi=doi,
            url=f"{self._content_url}/{doi}{version_suffix}",
            category=category,
            version=version,
            publication_types=("Preprint",),
        )


class BioRxivClient(_RxivClient):
    """Fetch and normalize bioRxiv papers from a date interval."""

    _server = "biorxiv"
    _source = PaperSource.BIORXIV
    _display_name = "bioRxiv"
    _content_url = "https://www.biorxiv.org/content"


class MedRxivClient(_RxivClient):
    """Fetch and normalize medRxiv papers from a date interval."""

    _server = "medrxiv"
    _source = PaperSource.MEDRXIV
    _display_name = "medRxiv"
    _content_url = "https://www.medrxiv.org/content"


def _parse_rxiv_response(
    response: httpx.Response, *, source_name: str
) -> tuple[list[dict[str, Any]], int]:
    try:
        payload = response.json()
        messages = payload["messages"]
        records = payload["collection"]
        if not isinstance(messages, list) or not messages:
            raise TypeError("messages must be a non-empty list")
        if not isinstance(records, list) or not all(
            isinstance(record, dict) for record in records
        ):
            raise TypeError("collection must be a list of objects")
        total = int(messages[0]["total"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ResponseParseError(f"{source_name} returned malformed JSON") from exc
    return records, total


def _parse_authors(value: Any, *, source_name: str) -> tuple[Author, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        names = value.split(";")
    elif isinstance(value, list) and all(isinstance(name, str) for name in value):
        names = value
    else:
        raise ResponseParseError(f"{source_name} paper has an invalid authors field")
    return tuple(Author(name=name.strip()) for name in names if name.strip())


def _required_string(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return " ".join(value.split())


def _optional_string(value: Any, *, source_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ResponseParseError(f"{source_name} paper has a non-string text field")
    normalized = " ".join(value.split())
    return normalized or None
