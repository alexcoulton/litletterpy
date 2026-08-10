"""Run recurring categorized discovery, rendering, and delivery."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Protocol

from litletter.config import CategoryConfig, LitletterConfig
from litletter.delivery import DeliveryReceipt, Mailer
from litletter.discovery import discover_papers
from litletter.errors import (
    BootstrapRequiredError,
    DatabaseError,
    DeliveryError,
    DeliveryUncertainError,
    SummarizationConfigurationError,
    SummarizationError,
    SummarizationResponseError,
)
from litletter.models import Paper, PaperSource
from litletter.newsletter import RenderedNewsletter, render_newsletter
from litletter.query import filter_papers, parse_query
from litletter.storage import Database, PendingPaper, StoredEdition
from litletter.summarization import Summarizer, paper_input_hash

_LOGGER = logging.getLogger(__name__)


class PubMedSource(Protocol):
    def search(
        self,
        query: str,
        *,
        since: date | None = None,
        until: date | None = None,
        max_results: int | None = None,
    ) -> list[Paper]: ...

    def fetch(
        self,
        *,
        since: date,
        until: date,
        max_results: int | None = None,
    ) -> list[Paper]: ...


class BioRxivSource(Protocol):
    def fetch(
        self,
        *,
        since: date,
        until: date,
        max_results: int | None = None,
    ) -> list[Paper]: ...


@dataclass(frozen=True, slots=True)
class RunResult:
    """Observable result of one scheduled invocation."""

    since: date | None
    until: date | None
    matched: int
    unsent: int
    summaries_created: int
    summaries_cached: int
    summary_failures: int
    newsletter: RenderedNewsletter | None
    receipt: DeliveryReceipt | None
    dry_run: bool
    message: str


@dataclass(frozen=True, slots=True)
class SummaryStats:
    """Summary enrichment counts for one invocation."""

    created: int = 0
    cached: int = 0
    failed: int = 0
    no_abstract: int = 0


def calculate_window(
    config: LitletterConfig,
    database: Database,
    *,
    today: date,
    bootstrap: bool,
) -> tuple[date, date]:
    """Calculate an overlapping discovery window from durable state."""
    last_until = database.last_successful_until()
    if last_until is None:
        if not bootstrap:
            raise BootstrapRequiredError(
                "database has no successful run; inspect the initial window with "
                "'litletter run --bootstrap --dry-run'"
            )
        since = today - timedelta(days=config.discovery.initial_lookback_days)
    else:
        since = min(last_until, today) - timedelta(days=config.discovery.overlap_days)
    return since, today


def run_once(
    config: LitletterConfig,
    database: Database,
    *,
    pubmed: PubMedSource | None,
    biorxiv: BioRxivSource | None,
    mailer: Mailer | None,
    summarizer: Summarizer | None = None,
    today: date,
    bootstrap: bool = False,
    dry_run: bool = False,
    retry_open_edition: bool = False,
) -> RunResult:
    """Execute one safe, stateful Litletter invocation."""
    database.sync_categories(config.categories)
    open_edition = database.open_edition()
    if open_edition is not None:
        return _handle_open_edition(
            open_edition,
            database,
            mailer=mailer,
            dry_run=dry_run,
            retry=retry_open_edition,
        )

    since, until = calculate_window(config, database, today=today, bootstrap=bootstrap)
    interrupted = database.fail_interrupted_runs()
    if interrupted:
        _LOGGER.warning("Marked %d interrupted run(s) failed", interrupted)
    run_id = database.start_run(since, until)
    run_finished = False
    try:
        matched = discover_categories(
            config.categories,
            database,
            run_id=run_id,
            since=since,
            until=until,
            pubmed=pubmed,
            biorxiv=biorxiv,
        )
        items = database.unsent_papers()
        if not items:
            database.finish_run(run_id)
            run_finished = True
            return RunResult(
                since=since,
                until=until,
                matched=matched,
                unsent=0,
                summaries_created=0,
                summaries_cached=0,
                summary_failures=0,
                newsletter=None,
                receipt=None,
                dry_run=dry_run,
                message="No unsent matching papers",
            )

        items, summary_stats = enrich_pending_papers(
            database,
            items,
            summarizer=summarizer,
            failure_policy=config.summarization.failure_policy,
        )

        newsletter = render_newsletter(
            config.newsletter,
            config.categories,
            items,
            edition_date=today,
        )
        if dry_run:
            database.finish_run(run_id)
            run_finished = True
            return RunResult(
                since=since,
                until=until,
                matched=matched,
                unsent=len(items),
                summaries_created=summary_stats.created,
                summaries_cached=summary_stats.cached,
                summary_failures=summary_stats.failed,
                newsletter=newsletter,
                receipt=None,
                dry_run=True,
                message="Dry run rendered without creating an edition",
            )
        if mailer is None:
            raise DeliveryError("a mailer is required unless --dry-run is used")
        edition = database.create_edition(
            edition_id=newsletter.edition_id,
            subject=newsletter.subject,
            text_body=newsletter.text,
            html_body=newsletter.html,
            items=items,
        )
        receipt = _deliver(database, edition, newsletter, mailer)
        database.finish_run(run_id)
        run_finished = True
        return RunResult(
            since=since,
            until=until,
            matched=matched,
            unsent=len(items),
            summaries_created=summary_stats.created,
            summaries_cached=summary_stats.cached,
            summary_failures=summary_stats.failed,
            newsletter=newsletter,
            receipt=receipt,
            dry_run=False,
            message="Newsletter submitted",
        )
    except Exception as exc:
        if not run_finished:
            database.finish_run(run_id, error=str(exc))
        raise


def discover_categories(
    categories: Sequence[CategoryConfig],
    database: Database,
    *,
    run_id: int,
    since: date,
    until: date,
    pubmed: PubMedSource | None,
    biorxiv: BioRxivSource | None,
) -> int:
    """Discover every category, fetching bioRxiv only once per run."""
    needs_biorxiv = any(
        PaperSource.BIORXIV in category.sources for category in categories
    )
    if needs_biorxiv and biorxiv is None:
        raise ValueError("configured bioRxiv categories require a bioRxiv client")
    biorxiv_candidates = (
        biorxiv.fetch(since=since, until=until)
        if needs_biorxiv and biorxiv is not None
        else []
    )
    total = 0
    for category in categories:
        matches: dict[tuple[PaperSource, str], Paper] = {}
        if PaperSource.PUBMED in category.sources:
            if pubmed is None:
                raise ValueError("configured PubMed categories require a PubMed client")
            for paper in discover_papers(
                category.query,
                since=since,
                until=until,
                pubmed=pubmed,
            ):
                matches[(paper.source, paper.source_id)] = paper
        if PaperSource.BIORXIV in category.sources:
            query = parse_query(category.query)
            for paper in filter_papers(biorxiv_candidates, query):
                matches[(paper.source, paper.source_id)] = paper
        ordered = sorted(
            matches.values(),
            key=lambda paper: (
                paper.published_at or date.min,
                paper.source.value,
                paper.source_id,
            ),
            reverse=True,
        )
        total += database.save_matches(category.id, ordered, run_id=run_id)
        _LOGGER.info("Category %s matched %d papers", category.name, len(ordered))
    return total


def enrich_pending_papers(
    database: Database,
    items: list[PendingPaper],
    *,
    summarizer: Summarizer | None,
    failure_policy: str,
) -> tuple[list[PendingPaper], SummaryStats]:
    """Attach exact cached or newly generated summaries to pending papers."""
    if summarizer is None:
        return items, SummaryStats()
    enriched: list[PendingPaper] = []
    created = cached = failed = no_abstract = 0
    for item in items:
        paper = item.paper
        if not paper.abstract or not paper.abstract.strip():
            no_abstract += 1
            enriched.append(item)
            continue
        input_hash = paper_input_hash(paper)
        summary = database.find_summary(
            paper,
            provider=summarizer.provider,
            model=summarizer.model,
            prompt_hash=summarizer.prompt_hash,
            input_hash=input_hash,
        )
        if summary is not None:
            cached += 1
            enriched.append(replace(item, summary=summary))
            continue
        try:
            result = summarizer.summarize(paper)
            if (
                result.provider != summarizer.provider
                or result.model != summarizer.model
                or result.prompt_hash != summarizer.prompt_hash
                or result.input_hash != input_hash
            ):
                raise SummarizationResponseError(
                    "summarizer returned a result with a mismatched cache identity"
                )
        except SummarizationConfigurationError:
            raise
        except SummarizationError as exc:
            if failure_policy == "abort":
                raise
            failed += 1
            _LOGGER.warning(
                "Summary failed for %s:%s; using abstract: %s",
                paper.source.value,
                paper.source_id,
                exc,
            )
            enriched.append(item)
            continue
        database.save_summary(paper, result)
        created += 1
        enriched.append(replace(item, summary=result.paper_summary))
    return enriched, SummaryStats(created, cached, failed, no_abstract)


def _handle_open_edition(
    edition: StoredEdition,
    database: Database,
    *,
    mailer: Mailer | None,
    dry_run: bool,
    retry: bool,
) -> RunResult:
    newsletter = _newsletter_from_edition(edition)
    if dry_run:
        return RunResult(
            since=None,
            until=None,
            matched=0,
            unsent=edition.item_count,
            summaries_created=0,
            summaries_cached=0,
            summary_failures=0,
            newsletter=newsletter,
            receipt=None,
            dry_run=True,
            message=f"Previewing existing {edition.status} edition {edition.id}",
        )
    if edition.status == "sending":
        raise DatabaseError(
            f"edition {edition.id} has uncertain delivery state 'sending'; "
            "inspect Postmark before changing its state"
        )
    if not retry:
        raise DatabaseError(
            f"edition {edition.id} is {edition.status}; use --retry-open-edition "
            "after confirming it was not delivered"
        )
    if mailer is None:
        raise DeliveryError("a mailer is required to retry an edition")
    receipt = _deliver(database, edition, newsletter, mailer)
    return RunResult(
        since=None,
        until=None,
        matched=0,
        unsent=edition.item_count,
        summaries_created=0,
        summaries_cached=0,
        summary_failures=0,
        newsletter=newsletter,
        receipt=receipt,
        dry_run=False,
        message="Existing edition submitted",
    )


def _deliver(
    database: Database,
    edition: StoredEdition,
    newsletter: RenderedNewsletter,
    mailer: Mailer,
) -> DeliveryReceipt:
    delivery_id = database.begin_delivery(edition.id, provider="postmark")
    try:
        receipt = mailer.send(newsletter)
    except DeliveryUncertainError:
        # Leave both records in `sending`: the provider may have accepted the
        # message even though Litletter did not receive its acknowledgement.
        raise
    except Exception as exc:
        database.fail_delivery(delivery_id, error=str(exc))
        raise
    database.complete_delivery(delivery_id, message_id=receipt.message_id)
    return receipt


def _newsletter_from_edition(edition: StoredEdition) -> RenderedNewsletter:
    return RenderedNewsletter(
        edition_id=edition.id,
        subject=edition.subject,
        text=edition.text_body,
        html=edition.html_body,
    )
