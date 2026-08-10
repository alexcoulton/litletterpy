from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from litletter.errors import (
    SummarizationConfigurationError,
    SummarizationResponseError,
    SummarizationTemporaryError,
)
from litletter.models import Paper, PaperSource
from litletter.summarization import DeepSeekSummarizer, paper_input_hash


def paper() -> Paper:
    return Paper(
        source=PaperSource.PUBMED,
        source_id="123",
        title="A cancer study",
        abstract="The authors studied treatment X and report a smaller tumour.",
        authors=(),
        published_at=date(2026, 8, 9),
        updated_at=None,
        doi=None,
        url="https://pubmed.ncbi.nlm.nih.gov/123/",
        journal="Nature",
    )


def summarizer(
    http_client: httpx.Client,
    *,
    max_retries: int = 0,
    sleeper=lambda _: None,
) -> DeepSeekSummarizer:
    return DeepSeekSummarizer(
        profile_id="deepseek-default",
        api_key="secret",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        audience="scientists outside the specialty",
        max_words=100,
        max_retries=max_retries,
        http_client=http_client,
        sleeper=sleeper,
    )


def test_deepseek_returns_validated_structured_summary() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "completion-1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "takeaway": "Treatment X reduced tumour size.",
                                    "summary": (
                                        "The authors studied treatment X and report "
                                        "a smaller tumour in their experiment."
                                    ),
                                }
                            )
                        },
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 30},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = summarizer(client).summarize(paper())

    assert result.paper_summary.takeaway == "Treatment X reduced tumour size."
    assert result.provider == "deepseek:deepseek-default"
    assert result.input_hash == paper_input_hash(paper())
    assert result.input_tokens == 200
    request = requests[0]
    assert request.url == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer secret"
    payload = json.loads(request.content)
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert "untrusted source material" in payload["messages"][0]["content"]


def test_deepseek_retries_only_transient_failures() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, text="busy")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(SummarizationTemporaryError, match="2 attempts"),
    ):
        summarizer(client, max_retries=1, sleeper=sleeps.append).summarize(paper())

    assert attempts == 2
    assert sleeps == [1]


def test_deepseek_authentication_error_is_configuration_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid key")

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(SummarizationConfigurationError, match="invalid key"),
    ):
        summarizer(client).summarize(paper())


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not JSON",
        json.dumps({"takeaway": "Only one field"}),
        json.dumps({"takeaway": "", "summary": "Text"}),
    ],
)
def test_deepseek_rejects_invalid_structured_output(content: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": content}}]
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(SummarizationResponseError),
    ):
        summarizer(client).summarize(paper())
