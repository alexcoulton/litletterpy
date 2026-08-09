"""Run recurring categorized discovery, rendering, and delivery."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
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
)
from litletter.models import Paper, PaperSource
from litletter.newsletter import RenderedNewsletter, render_newsletter
from litletter.query import filter_papers, parse_query
from litletter.storage import Database, StoredEdition

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
    newsletter: RenderedNewsletter | None
    receipt: DeliveryReceipt | None
    dry_run: bool
    message: str


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
                since,
                until,
                matched,
                0,
                None,
                None,
                dry_run,
                "No unsent matching papers",
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
                since,
                until,
                matched,
                len(items),
                newsletter,
                None,
                True,
                "Dry run rendered without creating an edition",
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
            since,
            until,
            matched,
            len(items),
            newsletter,
            receipt,
            False,
            "Newsletter submitted",
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
            None,
            None,
            0,
            edition.item_count,
            newsletter,
            None,
            True,
            f"Previewing existing {edition.status} edition {edition.id}",
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
        None,
        None,
        0,
        edition.item_count,
        newsletter,
        receipt,
        False,
        "Existing edition submitted",
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
