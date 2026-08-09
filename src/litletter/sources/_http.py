"""Shared HTTP behavior for literature source clients."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from litletter.errors import ApiResponseError, FetchError

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class HttpRequester:
    """Perform polite HTTP requests with bounded retries."""

    def __init__(
        self,
        *,
        source: str,
        user_agent: str,
        timeout: float,
        max_retries: int,
        requests_per_second: float,
        client: httpx.Client | None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if requests_per_second < 0:
            raise ValueError("requests_per_second must not be negative")

        self._source = source
        self._user_agent = user_agent
        self._timeout = timeout
        self._max_retries = max_retries
        self._minimum_interval = 1 / requests_per_second if requests_per_second else 0.0
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent},
        )

    def close(self) -> None:
        """Close an internally-created HTTP client."""
        if self._owns_client:
            self._client.close()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Return a successful response, retrying transient failures."""
        headers = {"User-Agent": self._user_agent}
        headers.update(kwargs.pop("headers", {}))
        kwargs.setdefault("timeout", self._timeout)
        for attempt in range(self._max_retries + 1):
            self._pace_requests()
            try:
                response = self._client.request(
                    method,
                    url,
                    headers=headers,
                    **kwargs,
                )
            except httpx.RequestError as exc:
                if attempt == self._max_retries:
                    raise FetchError(
                        f"{self._source} request failed after "
                        f"{attempt + 1} attempts: {exc}"
                    ) from exc
                self._sleep(0.5 * (2**attempt))
                continue

            if (
                response.status_code in _RETRYABLE_STATUS_CODES
                and attempt < self._max_retries
            ):
                self._sleep(self._retry_delay(response, attempt))
                continue

            if response.is_error:
                detail = response.text.strip().replace("\n", " ")[:500]
                raise ApiResponseError(
                    self._source,
                    response.status_code,
                    detail or response.reason_phrase,
                )

            return response

        raise AssertionError("request retry loop terminated unexpectedly")

    def _pace_requests(self) -> None:
        if self._last_request_at is not None and self._minimum_interval:
            elapsed = self._monotonic() - self._last_request_at
            remaining = self._minimum_interval - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._monotonic()

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return 0.5 * (2**attempt)
