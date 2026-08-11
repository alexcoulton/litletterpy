from __future__ import annotations

import json

import httpx
import pytest

from litletter.delivery import PostmarkMailer, ResendMailer
from litletter.errors import DeliveryError, DeliveryUncertainError
from litletter.newsletter import RenderedNewsletter


def newsletter() -> RenderedNewsletter:
    return RenderedNewsletter(
        edition_id="2026-08-09-abc123",
        subject="Two papers",
        text="Plain text",
        html="<p>HTML</p>",
    )


def test_postmark_mailer_submits_expected_broadcast_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "ErrorCode": 0,
                "Message": "OK",
                "MessageID": "message-123",
                "To": "reader@example.com",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        mailer = PostmarkMailer(
            server_token="POSTMARK_API_TEST",
            from_address="sender@example.com",
            to=("reader@example.com",),
            message_stream="broadcasts",
            http_client=http_client,
        )
        receipt = mailer.send(newsletter())

    assert receipt.message_id == "message-123"
    assert receipt.provider == "postmark"
    request = requests[0]
    assert request.url == "https://api.postmarkapp.com/email"
    assert request.headers["x-postmark-server-token"] == "POSTMARK_API_TEST"
    payload = json.loads(request.content)
    assert payload["MessageStream"] == "broadcasts"
    assert payload["Metadata"]["litletter-edition"] == "2026-08-09-abc123"
    assert payload["TextBody"] == "Plain text"
    assert payload["HtmlBody"] == "<p>HTML</p>"


def test_postmark_mailer_reports_rejected_message() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="Sender Signature not found")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        mailer = PostmarkMailer(
            server_token="token",
            from_address="sender@example.com",
            to=("reader@example.com",),
            message_stream="broadcasts",
            http_client=http_client,
        )
        with pytest.raises(DeliveryError, match="Sender Signature"):
            mailer.send(newsletter())


def test_postmark_mailer_treats_server_failure_as_uncertain() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Unavailable")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        mailer = PostmarkMailer(
            server_token="token",
            from_address="sender@example.com",
            to=("reader@example.com",),
            message_stream="broadcasts",
            http_client=http_client,
        )
        with pytest.raises(DeliveryUncertainError, match="uncertain"):
            mailer.send(newsletter())


def test_resend_mailer_submits_expected_idempotent_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "email-123"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        mailer = ResendMailer(
            api_key="RESEND_API_TEST",
            from_address="Litletter <sender@example.com>",
            to=("reader@example.com",),
            http_client=http_client,
        )
        receipt = mailer.send(newsletter())

    assert receipt.provider == "resend"
    assert receipt.message_id == "email-123"
    request = requests[0]
    assert request.url == "https://api.resend.com/emails"
    assert request.headers["authorization"] == "Bearer RESEND_API_TEST"
    assert request.headers["idempotency-key"] == ("litletter/2026-08-09-abc123")
    payload = json.loads(request.content)
    assert payload["from"] == "Litletter <sender@example.com>"
    assert payload["to"] == ["reader@example.com"]
    assert payload["text"] == "Plain text"
    assert payload["html"] == "<p>HTML</p>"
    assert payload["tags"] == [
        {"name": "litletter-edition", "value": "2026-08-09-abc123"}
    ]


def test_resend_mailer_reports_rejected_message() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Domain is not verified"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        mailer = ResendMailer(
            api_key="key",
            from_address="sender@example.com",
            to=("reader@example.com",),
            http_client=http_client,
        )
        with pytest.raises(DeliveryError, match="Domain is not verified"):
            mailer.send(newsletter())


def test_resend_mailer_treats_server_failure_as_uncertain() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Unavailable")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        mailer = ResendMailer(
            api_key="key",
            from_address="sender@example.com",
            to=("reader@example.com",),
            http_client=http_client,
        )
        with pytest.raises(DeliveryUncertainError, match="uncertain"):
            mailer.send(newsletter())
