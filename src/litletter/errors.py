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


class QuerySyntaxError(LitletterError, ValueError):
    """A Litletter Boolean query is not valid."""

    def __init__(self, message: str, query: str, position: int) -> None:
        self.message = message
        self.query = query
        self.position = position
        self.line = query.count("\n", 0, position) + 1
        line_start = query.rfind("\n", 0, position) + 1
        self.column = position - line_start + 1
        super().__init__(f"{message} at line {self.line}, column {self.column}")


class JournalCatalogError(LitletterError, ValueError):
    """The bundled journal catalog is invalid."""


class UnknownJournalGroupError(LitletterError, ValueError):
    """A query refers to a journal group that is not defined."""

    def __init__(self, name: str, available: tuple[str, ...] = ()) -> None:
        self.name = name
        self.available = available
        detail = f"unknown journal group '{name}'"
        if available:
            detail += f"; available groups: {', '.join(available)}"
        super().__init__(detail)


class ConfigurationError(LitletterError, ValueError):
    """A Litletter JSON configuration is invalid."""


class DatabaseError(LitletterError):
    """Litletter's persistent state could not be read or updated."""


class BootstrapRequiredError(LitletterError):
    """An initial discovery window requires explicit user confirmation."""


class DeliveryError(LitletterError):
    """A newsletter could not be submitted to its email provider."""


class DeliveryUncertainError(DeliveryError):
    """Delivery may have occurred, so an automatic retry would be unsafe."""
