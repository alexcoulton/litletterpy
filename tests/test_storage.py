from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from litletter.config import CategoryConfig
from litletter.models import Author, Paper, PaperSource
from litletter.storage import Database
from litletter.summarization import PaperSummary, SummaryResult, paper_input_hash


def category(
    category_id: str = "nsc-cancer",
    *,
    query: str = "cancer",
    query_fingerprint: str | None = None,
) -> CategoryConfig:
    return CategoryConfig(
        id=category_id,
        name="NSC Cancer",
        query=query,
        sources=(PaperSource.PUBMED,),
        query_fingerprint=query_fingerprint,
    )


def paper(source_id: str = "1") -> Paper:
    return Paper(
        source=PaperSource.PUBMED,
        source_id=source_id,
        title="A cancer result",
        abstract="An abstract",
        authors=(Author("Ada Lovelace", "0000-0001-2345-6789"),),
        published_at=date(2026, 8, 9),
        updated_at=None,
        doi="10.1000/result",
        url=f"https://pubmed.ncbi.nlm.nih.gov/{source_id}/",
        journal="Nature",
        journal_abbreviation="Nature",
        journal_nlm_id="0410462",
        journal_issns=("1476-4687",),
    )


def test_database_tracks_runs_matches_and_global_delivery_deduplication(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "litletter.sqlite3")
    database.initialize()
    database.sync_categories([category()])
    run_id = database.start_run(date(2026, 8, 7), date(2026, 8, 9))

    assert database.save_matches("nsc-cancer", [paper()], run_id=run_id) == 1
    database.finish_run(run_id)

    pending = database.unsent_papers()
    assert len(pending) == 1
    assert pending[0].paper.authors[0].orcid == "0000-0001-2345-6789"
    assert pending[0].category_ids == ("nsc-cancer",)
    assert database.last_successful_until() == date(2026, 8, 9)

    edition = database.create_edition(
        edition_id="edition-1",
        subject="One paper",
        text_body="Text",
        html_body="<p>HTML</p>",
        items=pending,
    )
    assert edition.status == "draft"
    delivery_id = database.begin_delivery(edition.id, provider="postmark")
    database.complete_delivery(delivery_id, message_id="message-1")

    assert database.unsent_papers() == []
    status = database.status()
    assert status.papers == 1
    assert status.category_matches == 1
    assert status.submitted_editions == 1
    assert status.open_edition is None
    database.close()


def test_paper_matching_multiple_categories_is_one_pending_item(tmp_path: Path) -> None:
    database = Database(tmp_path / "litletter.sqlite3")
    database.initialize()
    database.sync_categories([category("first"), category("second")])
    run_id = database.start_run(date(2026, 8, 9), date(2026, 8, 9))
    database.save_matches("second", [paper()], run_id=run_id)
    database.save_matches("first", [paper()], run_id=run_id)

    pending = database.unsent_papers()

    assert len(pending) == 1
    assert pending[0].primary_category_id == "first"
    assert pending[0].category_ids == ("first", "second")
    database.close()


def test_changed_category_query_discards_old_unsent_memberships(tmp_path: Path) -> None:
    database = Database(tmp_path / "litletter.sqlite3")
    database.initialize()
    database.sync_categories([category(query="cancer")])
    run_id = database.start_run(date(2026, 8, 9), date(2026, 8, 9))
    database.save_matches("nsc-cancer", [paper()], run_id=run_id)

    database.sync_categories([category(query="oncology")])

    assert database.unsent_papers() == []
    database.close()


def test_changed_author_group_discards_old_unsent_memberships(tmp_path: Path) -> None:
    database = Database(tmp_path / "litletter.sqlite3")
    database.initialize()
    database.sync_categories(
        [category(query="author_group:watchlist", query_fingerprint="first")]
    )
    run_id = database.start_run(date(2026, 8, 9), date(2026, 8, 9))
    database.save_matches("nsc-cancer", [paper()], run_id=run_id)

    database.sync_categories(
        [category(query="author_group:watchlist", query_fingerprint="second")]
    )

    assert database.unsent_papers() == []
    database.close()


def test_failed_delivery_keeps_edition_open_and_papers_unsent(tmp_path: Path) -> None:
    database = Database(tmp_path / "litletter.sqlite3")
    database.initialize()
    database.sync_categories([category()])
    run_id = database.start_run(date(2026, 8, 9), date(2026, 8, 9))
    database.save_matches("nsc-cancer", [paper()], run_id=run_id)
    pending = database.unsent_papers()
    database.create_edition(
        edition_id="edition-1",
        subject="One paper",
        text_body="Text",
        html_body="HTML",
        items=pending,
    )
    delivery_id = database.begin_delivery("edition-1", provider="postmark")

    database.fail_delivery(delivery_id, error="provider unavailable")

    assert database.open_edition().status == "failed"
    assert len(database.unsent_papers()) == 1
    database.close()


def test_operator_can_resolve_an_uncertain_delivery(tmp_path: Path) -> None:
    database = Database(tmp_path / "litletter.sqlite3")
    database.initialize()
    database.sync_categories([category()])
    run_id = database.start_run(date(2026, 8, 9), date(2026, 8, 9))
    database.save_matches("nsc-cancer", [paper()], run_id=run_id)
    database.create_edition(
        edition_id="edition-1",
        subject="One paper",
        text_body="Text",
        html_body="HTML",
        items=database.unsent_papers(),
    )
    database.begin_delivery("edition-1", provider="postmark")

    database.resolve_uncertain_delivery(
        "edition-1", delivered=True, message_id="postmark-message"
    )

    edition = database.get_edition("edition-1")
    assert edition.status == "submitted"
    assert edition.message_id == "postmark-message"
    assert database.unsent_papers() == []
    database.close()


def test_database_caches_summaries_by_full_identity(tmp_path: Path) -> None:
    database = Database(tmp_path / "litletter.sqlite3")
    database.initialize()
    database.sync_categories([category()])
    run_id = database.start_run(date(2026, 8, 9), date(2026, 8, 9))
    result_paper = paper()
    database.save_matches("nsc-cancer", [result_paper], run_id=run_id)
    input_hash = paper_input_hash(result_paper)
    result = SummaryResult(
        paper_summary=PaperSummary("A takeaway.", "An accessible summary."),
        provider="deepseek:default",
        model="deepseek-v4-flash",
        prompt_hash="prompt-hash",
        input_hash=input_hash,
        input_tokens=100,
        output_tokens=25,
        provider_request_id="request-1",
    )

    database.save_summary(result_paper, result)

    assert database.find_summary(
        result_paper,
        provider="deepseek:default",
        model="deepseek-v4-flash",
        prompt_hash="prompt-hash",
        input_hash=input_hash,
    ) == PaperSummary("A takeaway.", "An accessible summary.")
    assert (
        database.find_summary(
            result_paper,
            provider="deepseek:default",
            model="different-model",
            prompt_hash="prompt-hash",
            input_hash=input_hash,
        )
        is None
    )
    assert database.status().cached_summaries == 1
    database.close()


def test_database_migrates_schema_one_to_summary_cache(tmp_path: Path) -> None:
    path = tmp_path / "litletter.sqlite3"
    database = Database(path)
    database.initialize()
    database.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE paper_summaries")
        connection.execute("PRAGMA user_version = 1")

    database = Database(path)
    database.initialize()

    assert database.connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert database.status().cached_summaries == 0
    database.close()
