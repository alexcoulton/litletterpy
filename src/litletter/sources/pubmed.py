"""PubMed E-utilities client."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx

from litletter.errors import PubMedResultLimitError, ResponseParseError
from litletter.models import Author, Paper, PaperSource
from litletter.sources._http import HttpRequester

_LOGGER = logging.getLogger(__name__)

_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_MAX_ACCESSIBLE_RESULTS = 10_000
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


class PubMedClient:
    """Fetch and normalize papers through PubMed ESearch and EFetch."""

    def __init__(
        self,
        *,
        email: str,
        api_key: str | None = None,
        tool: str = "litletter",
        timeout: float = 20.0,
        max_retries: int = 3,
        search_page_size: int = 5_000,
        fetch_batch_size: int = 200,
        requests_per_second: float | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Create a client.

        ``email`` and ``tool`` identify the caller to NCBI. By default requests
        are paced at NCBI's documented limit of three per second, or ten per
        second when an API key is supplied. Set ``requests_per_second=0`` to
        disable pacing, which is useful with a mocked transport.
        """
        if not email.strip():
            raise ValueError("email must not be empty")
        if not tool.strip():
            raise ValueError("tool must not be empty")
        if not 1 <= search_page_size <= _MAX_ACCESSIBLE_RESULTS:
            raise ValueError("search_page_size must be between 1 and 10,000")
        if fetch_batch_size < 1:
            raise ValueError("fetch_batch_size must be greater than zero")

        self._email = email
        self._api_key = api_key
        self._tool = tool
        self._search_page_size = search_page_size
        self._fetch_batch_size = fetch_batch_size
        request_rate = requests_per_second
        if request_rate is None:
            request_rate = 10.0 if api_key else 3.0
        self._http = HttpRequester(
            source="PubMed",
            user_agent=f"{tool}/0.1 (contact: {email})",
            timeout=timeout,
            max_retries=max_retries,
            requests_per_second=request_rate,
            client=http_client,
        )

    def __enter__(self) -> PubMedClient:
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
        since: date | None = None,
        until: date | None = None,
        max_results: int | None = None,
    ) -> list[Paper]:
        """Search PubMed and return normalized papers.

        ``query`` uses PubMed's native query syntax. Date bounds constrain the
        PubMed Entrez date (``EDAT``), which reflects when a record entered the
        database and is suitable for recurring discovery jobs.
        """
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        _validate_date_range(since, until)
        _validate_max_results(max_results)
        if max_results == 0:
            return []

        term = _with_date_range(query, since, until)
        return self._retrieve(term, max_results=max_results)

    def fetch(
        self,
        *,
        since: date,
        until: date,
        max_results: int | None = None,
    ) -> list[Paper]:
        """Return every PubMed record entering the database in a date range."""
        _validate_date_range(since, until)
        _validate_max_results(max_results)
        if max_results == 0:
            return []
        return self._retrieve(
            _date_range_filter(since, until),
            max_results=max_results,
        )

    def _retrieve(self, term: str, *, max_results: int | None) -> list[Paper]:
        identifiers = self._search_ids(term, max_results=max_results)
        papers: list[Paper] = []
        for batch in _batched(identifiers, self._fetch_batch_size):
            papers.extend(self._fetch_batch(batch))
        return sorted(
            papers,
            key=lambda paper: (paper.published_at or date.min, paper.source_id),
            reverse=True,
        )

    def _search_ids(self, term: str, *, max_results: int | None) -> list[str]:
        identifiers: list[str] = []
        total: int | None = None
        target: int | None = None

        while target is None or len(identifiers) < target:
            page_size = self._search_page_size
            if target is not None:
                page_size = min(page_size, target - len(identifiers))

            params = self._common_parameters()
            params.update(
                {
                    "db": "pubmed",
                    "term": term,
                    "retmode": "json",
                    "retstart": str(len(identifiers)),
                    "retmax": str(page_size),
                    "sort": "pub_date",
                }
            )
            _LOGGER.debug(
                "Requesting PubMed ESearch page at offset %d with size %d",
                len(identifiers),
                page_size,
            )
            if len(term) > 400:
                response = self._http.request("POST", _ESEARCH_URL, data=params)
            else:
                response = self._http.request("GET", _ESEARCH_URL, params=params)
            result = _parse_esearch_response(response)

            if total is None:
                total = result["count"]
                requested = total if max_results is None else min(total, max_results)
                if requested > _MAX_ACCESSIBLE_RESULTS:
                    raise PubMedResultLimitError(
                        "PubMed ESearch exposes only the first 10,000 results; "
                        "use a narrower query or date range"
                    )
                target = requested
                _LOGGER.debug(
                    "PubMed ESearch reported %d results; retrieving %d",
                    total,
                    target,
                )

            page = result["ids"]
            identifiers.extend(page[: max(0, target - len(identifiers))])
            if not page or len(identifiers) >= target:
                break

        return identifiers

    def _fetch_batch(self, identifiers: list[str]) -> list[Paper]:
        _LOGGER.debug("Fetching a PubMed batch of %d records", len(identifiers))
        data = self._common_parameters()
        data.update(
            {
                "db": "pubmed",
                "id": ",".join(identifiers),
                "retmode": "xml",
            }
        )
        response = self._http.request("POST", _EFETCH_URL, data=data)
        return _parse_pubmed_xml(response.text)

    def _common_parameters(self) -> dict[str, str]:
        parameters = {"tool": self._tool, "email": self._email}
        if self._api_key:
            parameters["api_key"] = self._api_key
        return parameters


def _parse_esearch_response(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
        result = payload["esearchresult"]
        count = int(result["count"])
        identifiers = result["idlist"]
        if not isinstance(identifiers, list) or not all(
            isinstance(identifier, str) for identifier in identifiers
        ):
            raise TypeError("idlist must be a list of strings")
    except (KeyError, TypeError, ValueError) as exc:
        raise ResponseParseError("PubMed returned malformed ESearch JSON") from exc
    return {"count": count, "ids": identifiers}


def _parse_pubmed_xml(content: str) -> list[Paper]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ResponseParseError("PubMed returned malformed EFetch XML") from exc

    papers: list[Paper] = []
    try:
        for record in root.findall("./PubmedArticle"):
            papers.append(_parse_pubmed_record(record))
    except (TypeError, ValueError) as exc:
        raise ResponseParseError("PubMed returned an invalid article record") from exc
    return papers


def _parse_pubmed_record(record: ET.Element) -> Paper:
    citation = record.find("./MedlineCitation")
    article = record.find("./MedlineCitation/Article")
    if citation is None or article is None:
        raise ValueError("article is missing its citation")

    pmid = _text(citation.find("./PMID"))
    title = _text(article.find("./ArticleTitle"))
    if not pmid or not title:
        raise ValueError("article is missing a PMID or title")

    abstract_parts: list[str] = []
    for abstract in article.findall("./Abstract/AbstractText"):
        text = _text(abstract)
        if not text:
            continue
        label = abstract.get("Label")
        abstract_parts.append(f"{label}: {text}" if label else text)

    authors: list[Author] = []
    for element in article.findall("./AuthorList/Author"):
        collective_name = _text(element.find("./CollectiveName"))
        if collective_name:
            name = collective_name
        else:
            first_name = _text(element.find("./ForeName")) or _text(
                element.find("./Initials")
            )
            last_name = _text(element.find("./LastName"))
            suffix = _text(element.find("./Suffix"))
            name = " ".join(part for part in (first_name, last_name, suffix) if part)
        if name:
            orcid = None
            for identifier in element.findall("./Identifier"):
                if identifier.get("Source", "").lower() == "orcid":
                    orcid = _text(identifier)
                    break
            authors.append(Author(name=name, orcid=orcid))

    published_at = None
    article_date = article.find("./ArticleDate")
    journal_date = article.find("./Journal/JournalIssue/PubDate")
    if article_date is not None:
        published_at = _parse_pubmed_date(article_date)
    if published_at is None and journal_date is not None:
        published_at = _parse_pubmed_date(journal_date)

    revised = citation.find("./DateRevised")
    updated_at = _parse_pubmed_date(revised) if revised is not None else None

    doi = None
    for identifier in record.findall("./PubmedData/ArticleIdList/ArticleId"):
        if identifier.get("IdType", "").lower() == "doi":
            doi = _text(identifier)
            break
    if doi is None:
        for identifier in article.findall("./ELocationID"):
            if identifier.get("EIdType", "").lower() == "doi":
                doi = _text(identifier)
                break

    journal = _text(article.find("./Journal/Title")) or _text(
        article.find("./Journal/ISOAbbreviation")
    )
    return Paper(
        source=PaperSource.PUBMED,
        source_id=pmid,
        title=title,
        abstract="\n".join(abstract_parts) or None,
        authors=tuple(authors),
        published_at=published_at,
        updated_at=updated_at,
        doi=doi,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        journal=journal,
    )


def _parse_pubmed_date(element: ET.Element) -> date | None:
    year_text = _text(element.find("./Year"))
    month_text = _text(element.find("./Month"))
    day_text = _text(element.find("./Day"))

    if not year_text:
        medline_date = _text(element.find("./MedlineDate"))
        if not medline_date:
            return None
        year_match = re.search(r"\b(\d{4})\b", medline_date)
        if not year_match:
            return None
        year_text = year_match.group(1)
        month_match = re.search(r"\b([A-Za-z]{3,9})\b", medline_date)
        month_text = month_match.group(1) if month_match else None

    try:
        year = int(year_text)
        month = _parse_month(month_text) if month_text else 1
        day = int(day_text) if day_text else 1
        return date(year, month, day)
    except (KeyError, ValueError):
        return None


def _parse_month(value: str) -> int:
    normalized = value.strip().lower().rstrip(".")
    if normalized.isdigit():
        return int(normalized)
    return _MONTHS[normalized]


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = "".join(element.itertext())
    normalized = " ".join(value.split())
    return normalized or None


def _with_date_range(query: str, since: date | None, until: date | None) -> str:
    if since is None and until is None:
        return query
    return f"({query}) AND ({_date_range_filter(since, until)})"


def _date_range_filter(since: date | None, until: date | None) -> str:
    lower = since.strftime("%Y/%m/%d") if since else "1900"
    upper = until.strftime("%Y/%m/%d") if until else "3000"
    return f"{lower}:{upper}[EDAT]"


def _validate_date_range(since: date | None, until: date | None) -> None:
    if since is not None and until is not None and since > until:
        raise ValueError("since must not be after until")


def _validate_max_results(max_results: int | None) -> None:
    if max_results is not None and max_results < 0:
        raise ValueError("max_results must not be negative")


def _batched(values: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]
