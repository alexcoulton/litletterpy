"""Exceptions raised by Litletter's source clients."""


class LitletterError(Exception):
    """Base class for package-specific errors."""


class FetchError(LitletterError):
    """A source request could not be completed."""


class ApiResponseError(FetchError):
    """A source API returned an unsuccessful HTTP response."""

    def __init__(self, source: str, status_code: int, detail: str) -> None:
        self.source = source
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{source} returned HTTP {status_code}: {detail}")


class ResponseParseError(FetchError):
    """A source API returned a response that could not be interpreted."""


class PubMedResultLimitError(FetchError):
    """A PubMed query exceeds the records accessible through ESearch."""
