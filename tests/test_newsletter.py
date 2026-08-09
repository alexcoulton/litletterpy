from __future__ import annotations

from datetime import date

from litletter.config import CategoryConfig, NewsletterConfig
from litletter.models import Author, Paper, PaperSource
from litletter.newsletter import render_newsletter
from litletter.storage import PendingPaper


def paper(source_id: str, title: str, *, abstract: str | None) -> Paper:
    return Paper(
        source=PaperSource.PUBMED,
        source_id=source_id,
        title=title,
        abstract=abstract,
        authors=tuple(Author(f"Author {index}") for index in range(10)),
        published_at=date(2026, 8, 9),
        updated_at=None,
        doi="10.1000/example",
        url=f"https://example.test/?paper={source_id}&view=full",
        journal="Nature",
    )


def test_renderer_groups_papers_and_escapes_html() -> None:
    categories = (
        CategoryConfig(
            id="cancer",
            name="Cancer & Oncology",
            query="cancer",
            sources=(PaperSource.PUBMED,),
        ),
        CategoryConfig(
            id="methods",
            name="Methods",
            query="methods",
            sources=(PaperSource.PUBMED,),
        ),
    )
    config = NewsletterConfig(
        title="My <Litletter>",
        from_address="sender@example.com",
        to=("reader@example.com",),
        timezone="Europe/London",
        abstract_max_characters=24,
    )
    items = [
        PendingPaper(
            paper=paper(
                "1",
                "Cancer <script>alert(1)</script>",
                abstract="A long abstract containing useful scientific details.",
            ),
            primary_category_id="cancer",
            category_ids=("cancer", "methods"),
        )
    ]

    rendered = render_newsletter(
        config,
        categories,
        items,
        edition_date=date(2026, 8, 9),
    )

    assert rendered.subject == "My <Litletter> — 1 new paper — 2026-08-09"
    assert rendered.edition_id.startswith("2026-08-09-")
    assert "## Cancer & Oncology (1)" in rendered.text
    assert "Also in: Methods" in rendered.text
    assert "Author 7, et al." in rendered.text
    assert "A long abstract…" in rendered.text
    assert "<script>" not in rendered.html
    assert "Cancer &lt;script&gt;" in rendered.html
    assert "paper=1&amp;view=full" in rendered.html
    assert "Cancer &amp; Oncology" in rendered.html


def test_edition_id_is_stable_for_same_content() -> None:
    category = CategoryConfig(
        id="cancer",
        name="Cancer",
        query="cancer",
        sources=(PaperSource.PUBMED,),
    )
    config = NewsletterConfig(
        title="Litletter",
        from_address="sender@example.com",
        to=("reader@example.com",),
        timezone="UTC",
        abstract_max_characters=0,
    )
    items = [PendingPaper(paper("1", "Result", abstract=None), "cancer", ("cancer",))]

    first = render_newsletter(config, (category,), items, edition_date=date(2026, 8, 9))
    second = render_newsletter(
        config, (category,), items, edition_date=date(2026, 8, 9)
    )

    assert first == second
