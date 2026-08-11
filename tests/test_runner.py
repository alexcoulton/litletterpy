from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import pytest

from litletter.config import (
    ArXivConfig,
    BioRxivConfig,
    CategoryConfig,
    DeliveryConfig,
    DiscoveryConfig,
    LitletterConfig,
    MedRxivConfig,
    NewsletterConfig,
    PubMedConfig,
    SummarizationConfig,
)
from litletter.delivery import DeliveryReceipt
from litletter.errors import (
    BootstrapRequiredError,
    DatabaseError,
    DeliveryUncertainError,
)
from litletter.models import Paper, PaperSource
from litletter.newsletter import RenderedNewsletter
from litletter.runner import calculate_window, run_once
from litletter.storage import Database
from litletter.summarization import (
    PaperSummary,
    SummaryResult,
    paper_input_hash,
)


@dataclass
class FakePubMed:
    papers: list[Paper]
    searches: list[tuple[str, date | None, date | None]] = field(default_factory=list)
    fetches: list[tuple[date, date, int | None]] = field(default_factory=list)

    def search(
        self,
        query: str,
        *,
        since: date | None = None,
        until: date | None = None,
        max_results: int | None = None,
    ) -> list[Paper]:
        self.searches.append((query, since, until))
        return self.papers

    def fetch(
        self,
        *,
        since: date,
        until: date,
        max_results: int | None = None,
    ) -> list[Paper]:
        self.fetches.append((since, until, max_results))
        return self.papers


@dataclass
class FakeMailer:
    provider: str = "postmark"
    newsletters: list[RenderedNewsletter] = field(default_factory=list)

    def send(self, newsletter: RenderedNewsletter) -> DeliveryReceipt:
        self.newsletters.append(newsletter)
        return DeliveryReceipt(provider=self.provider, message_id="message-1")


class UncertainMailer:
    provider = "postmark"

    def send(self, newsletter: RenderedNewsletter) -> DeliveryReceipt:
        raise DeliveryUncertainError("request timed out after submission")


@dataclass
class FakeSummarizer:
    provider: str = "deepseek:test"
    model: str = "deepseek-v4-flash"
    prompt_hash: str = "prompt-v1"
    calls: list[str] = field(default_factory=list)

    def summarize(self, value: Paper) -> SummaryResult:
        self.calls.append(value.source_id)
        return SummaryResult(
            paper_summary=PaperSummary(
                "The study reports a cancer result.",
                "The authors studied cancer biology and report a result.",
            ),
            provider=self.provider,
            model=self.model,
            prompt_hash=self.prompt_hash,
            input_hash=paper_input_hash(value),
            input_tokens=100,
            output_tokens=20,
            provider_request_id="request-1",
        )


def config(tmp_path: Path) -> LitletterConfig:
    return LitletterConfig(
        path=tmp_path / "litletter.json",
        database=tmp_path / "litletter.sqlite3",
        newsletter=NewsletterConfig(
            title="My Litletter",
            from_address="sender@example.com",
            to=("reader@example.com",),
            timezone="Europe/London",
            abstract_max_characters=800,
        ),
        pubmed=PubMedConfig(True, "pubmed-default"),
        biorxiv=BioRxivConfig(False),
        medrxiv=MedRxivConfig(False),
        arxiv=ArXivConfig(False),
        discovery=DiscoveryConfig(initial_lookback_days=30, overlap_days=2),
        categories=(
            CategoryConfig(
                id="cancer",
                name="Cancer",
                query="title_abstract:cancer",
                sources=(PaperSource.PUBMED,),
            ),
            CategoryConfig(
                id="nature",
                name="Nature",
                query="title_abstract:cancer AND journal:Nature",
                sources=(PaperSource.PUBMED,),
            ),
        ),
        summarization=SummarizationConfig(
            enabled=False,
            provider=None,
            model="deepseek-v4-flash",
            max_words=100,
            audience="scientists",
            failure_policy="fallback",
        ),
        delivery=DeliveryConfig("postmark-default", "broadcasts"),
    )


def paper() -> Paper:
    return Paper(
        source=PaperSource.PUBMED,
        source_id="123",
        title="Cancer biology",
        abstract="A result.",
        authors=(),
        published_at=date(2026, 8, 8),
        updated_at=None,
        doi="10.1000/example",
        url="https://pubmed.ncbi.nlm.nih.gov/123/",
        journal="Nature",
    )


def open_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "litletter.sqlite3")
    database.initialize()
    return database


def test_initial_window_requires_explicit_bootstrap(tmp_path: Path) -> None:
    settings = config(tmp_path)
    database = open_database(tmp_path)

    with pytest.raises(BootstrapRequiredError):
        calculate_window(settings, database, today=date(2026, 8, 9), bootstrap=False)

    assert calculate_window(
        settings, database, today=date(2026, 8, 9), bootstrap=True
    ) == (date(2026, 7, 10), date(2026, 8, 9))
    database.close()


def test_run_sends_one_categorized_edition_and_deduplicates_future_runs(
    tmp_path: Path,
) -> None:
    settings = config(tmp_path)
    database = open_database(tmp_path)
    pubmed = FakePubMed([paper()])
    mailer = FakeMailer()

    first = run_once(
        settings,
        database,
        pubmed=pubmed,
        biorxiv=None,
        mailer=mailer,
        today=date(2026, 8, 9),
        bootstrap=True,
    )

    assert first.matched == 2
    assert first.unsent == 1
    assert len(mailer.newsletters) == 1
    assert "Cancer" in mailer.newsletters[0].text
    assert "Also in: Nature" in mailer.newsletters[0].text
    assert database.status().submitted_editions == 1
    assert database.unsent_papers() == []
    assert (
        database.connection.execute("SELECT provider FROM deliveries").fetchone()[0]
        == "postmark"
    )

    second = run_once(
        settings,
        database,
        pubmed=pubmed,
        biorxiv=None,
        mailer=mailer,
        today=date(2026, 8, 10),
    )

    assert second.since == date(2026, 8, 7)
    assert second.unsent == 0
    assert len(mailer.newsletters) == 1
    database.close()


def test_runner_records_the_selected_mailer_provider(tmp_path: Path) -> None:
    settings = config(tmp_path)
    database = open_database(tmp_path)

    result = run_once(
        settings,
        database,
        pubmed=FakePubMed([paper()]),
        biorxiv=None,
        mailer=FakeMailer(provider="resend"),
        today=date(2026, 8, 9),
        bootstrap=True,
    )

    assert result.receipt is not None
    assert result.receipt.provider == "resend"
    assert (
        database.connection.execute("SELECT provider FROM deliveries").fetchone()[0]
        == "resend"
    )
    database.close()


def test_dry_run_advances_discovery_but_does_not_create_edition(
    tmp_path: Path,
) -> None:
    settings = config(tmp_path)
    database = open_database(tmp_path)

    result = run_once(
        settings,
        database,
        pubmed=FakePubMed([paper()]),
        biorxiv=None,
        mailer=None,
        today=date(2026, 8, 9),
        bootstrap=True,
        dry_run=True,
    )

    assert result.newsletter is not None
    assert result.dry_run is True
    assert database.open_edition() is None
    assert len(database.unsent_papers()) == 1
    assert database.last_successful_until() == date(2026, 8, 9)
    database.close()


def test_run_discovers_medrxiv_and_arxiv_categories(tmp_path: Path) -> None:
    settings = config(tmp_path)
    settings = replace(
        settings,
        medrxiv=MedRxivConfig(True),
        arxiv=ArXivConfig(True),
        categories=(
            CategoryConfig(
                id="preprints",
                name="Preprints",
                query="title_abstract:cancer",
                sources=(PaperSource.MEDRXIV, PaperSource.ARXIV),
            ),
        ),
    )
    medrxiv_paper = replace(
        paper(),
        source=PaperSource.MEDRXIV,
        source_id="med-1",
        doi="10.1101/med-1",
        url="https://www.medrxiv.org/content/10.1101/med-1",
    )
    arxiv_paper = replace(
        paper(),
        source=PaperSource.ARXIV,
        source_id="2608.12345",
        doi=None,
        url="https://arxiv.org/abs/2608.12345",
    )
    medrxiv = FakePubMed([medrxiv_paper])
    arxiv = FakePubMed([arxiv_paper])
    database = open_database(tmp_path)

    result = run_once(
        settings,
        database,
        pubmed=None,
        biorxiv=None,
        medrxiv=medrxiv,
        arxiv=arxiv,
        mailer=None,
        today=date(2026, 8, 9),
        bootstrap=True,
        dry_run=True,
    )

    assert result.matched == 2
    assert result.unsent == 2
    assert medrxiv.searches == []
    assert medrxiv.fetches == [(date(2026, 7, 10), date(2026, 8, 9), None)]
    assert arxiv.searches == [
        ("(ti:cancer OR abs:cancer)", date(2026, 7, 10), date(2026, 8, 9))
    ]
    database.close()


def test_open_failed_edition_requires_explicit_retry(tmp_path: Path) -> None:
    settings = config(tmp_path)
    database = open_database(tmp_path)
    mailer = FakeMailer()
    run_once(
        settings,
        database,
        pubmed=FakePubMed([paper()]),
        biorxiv=None,
        mailer=mailer,
        today=date(2026, 8, 9),
        bootstrap=True,
    )
    edition = database.connection.execute("SELECT id FROM editions").fetchone()[0]
    database.connection.execute(
        "UPDATE editions SET status = 'failed' WHERE id = ?", (edition,)
    )
    database.connection.commit()

    with pytest.raises(DatabaseError, match="retry-open-edition"):
        run_once(
            settings,
            database,
            pubmed=FakePubMed([]),
            biorxiv=None,
            mailer=mailer,
            today=date(2026, 8, 10),
        )

    retry_mailer = FakeMailer()
    result = run_once(
        settings,
        database,
        pubmed=FakePubMed([]),
        biorxiv=None,
        mailer=retry_mailer,
        today=date(2026, 8, 10),
        retry_open_edition=True,
    )
    assert result.message == "Existing edition submitted"
    assert len(retry_mailer.newsletters) == 1
    database.close()


def test_uncertain_delivery_stops_future_runs_until_operator_resolution(
    tmp_path: Path,
) -> None:
    settings = config(tmp_path)
    database = open_database(tmp_path)

    with pytest.raises(DeliveryUncertainError):
        run_once(
            settings,
            database,
            pubmed=FakePubMed([paper()]),
            biorxiv=None,
            mailer=UncertainMailer(),
            today=date(2026, 8, 9),
            bootstrap=True,
        )

    edition = database.open_edition()
    assert edition is not None
    assert edition.status == "sending"
    with pytest.raises(DatabaseError, match="uncertain delivery state"):
        run_once(
            settings,
            database,
            pubmed=FakePubMed([]),
            biorxiv=None,
            mailer=FakeMailer(),
            today=date(2026, 8, 10),
        )

    database.resolve_uncertain_delivery(edition.id, delivered=False)
    assert database.open_edition().status == "failed"
    database.close()


def test_run_creates_and_reuses_cached_summaries(tmp_path: Path) -> None:
    settings = config(tmp_path)
    settings = replace(
        settings,
        summarization=replace(
            settings.summarization,
            enabled=True,
            provider="deepseek-default",
        ),
    )
    database = open_database(tmp_path)
    first_summarizer = FakeSummarizer()

    first = run_once(
        settings,
        database,
        pubmed=FakePubMed([paper()]),
        biorxiv=None,
        mailer=None,
        summarizer=first_summarizer,
        today=date(2026, 8, 9),
        bootstrap=True,
        dry_run=True,
    )

    assert first.summaries_created == 1
    assert first.summaries_cached == 0
    assert first_summarizer.calls == ["123"]
    assert "Takeaway:" in first.newsletter.text

    second_summarizer = FakeSummarizer()
    second = run_once(
        settings,
        database,
        pubmed=FakePubMed([paper()]),
        biorxiv=None,
        mailer=None,
        summarizer=second_summarizer,
        today=date(2026, 8, 10),
        dry_run=True,
    )

    assert second.summaries_created == 0
    assert second.summaries_cached == 1
    assert second_summarizer.calls == []
    database.close()
