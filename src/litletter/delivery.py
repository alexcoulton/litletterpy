"""Provider-neutral delivery contracts and Postmark integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from litletter.errors import DeliveryError, DeliveryUncertainError
from litletter.newsletter import RenderedNewsletter

_POSTMARK_EMAIL_URL = "https://api.postmarkapp.com/email"


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """A provider acknowledgement for a submitted newsletter."""

    provider: str
    message_id: str


class Mailer(Protocol):
    """Submit a rendered newsletter to an email provider."""

    def send(self, newsletter: RenderedNewsletter) -> DeliveryReceipt: ...


class PostmarkMailer:
    """Submit newsletters through Postmark's single-email API."""

    def __init__(
        self,
        *,
        server_token: str,
        from_address: str,
        to: tuple[str, ...],
        message_stream: str,
        timeout: float = 20.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not server_token.strip():
            raise ValueError("server_token must not be empty")
        if not from_address.strip():
            raise ValueError("from_address must not be empty")
        if not to:
            raise ValueError("at least one recipient is required")
        if not message_stream.strip():
            raise ValueError("message_stream must not be empty")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self._token = server_token
        self._from_address = from_address
        self._to = to
        self._message_stream = message_stream
        self._timeout = timeout
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)

    def __enter__(self) -> PostmarkMailer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def send(self, newsletter: RenderedNewsletter) -> DeliveryReceipt:
        """Submit exactly one request; uncertain failures are never retried."""
        payload = {
            "From": self._from_address,
            "To": ",".join(self._to),
            "Subject": newsletter.subject,
            "TextBody": newsletter.text,
            "HtmlBody": newsletter.html,
            "MessageStream": self._message_stream,
            "Tag": "litletter",
            "TrackOpens": False,
            "TrackLinks": "None",
            "Metadata": {"litletter-edition": newsletter.edition_id},
            "Headers": [
                {
                    "Name": "Message-ID",
                    "Value": f"<litletter-{newsletter.edition_id}@litletter.local>",
                }
            ],
        }
        try:
            response = self._http.post(
                _POSTMARK_EMAIL_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Postmark-Server-Token": self._token,
                },
                json=payload,
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise DeliveryUncertainError(
                f"Postmark request outcome is uncertain: {exc}"
            ) from exc
        if response.status_code >= 500:
            raise DeliveryUncertainError(
                f"Postmark returned HTTP {response.status_code}; delivery is uncertain"
            )
        if response.is_error:
            detail = response.text.strip().replace("\n", " ")[:500]
            raise DeliveryError(
                f"Postmark returned HTTP {response.status_code}: {detail}"
            )
        try:
            body = response.json()
            error_code = int(body["ErrorCode"])
            message_id = str(body["MessageID"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DeliveryUncertainError(
                "Postmark returned an invalid success response; delivery is uncertain"
            ) from exc
        if error_code != 0:
            detail = body.get("Message", "")
            raise DeliveryError(
                f"Postmark rejected the message ({error_code}): {detail}"
            )
        if not message_id.strip():
            raise DeliveryUncertainError(
                "Postmark returned no message ID; delivery is uncertain"
            )
        return DeliveryReceipt(provider="postmark", message_id=message_id)
