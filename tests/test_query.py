from __future__ import annotations

import re
from datetime import date

import pytest

from litletter import QuerySyntaxError
from litletter.models import Paper, PaperSource
from litletter.query import And, Field, Not, Or, Term, filter_papers, parse_query


def paper(
    title: str,
    abstract: str | None = None,
    *,
    source: PaperSource = PaperSource.PUBMED,
    source_id: str = "1",
    journal: str | None = None,
    category: str | None = None,
    journal_abbreviation: str | None = None,
    journal_nlm_id: str | None = None,
    journal_issns: tuple[str, ...] = (),
    publication_types: tuple[str, ...] = (),
) -> Paper:
    return Paper(
        source=source,
        source_id=source_id,
        title=title,
        abstract=abstract,
        authors=(),
        published_at=date(2026, 8, 9),
        updated_at=None,
        doi=None,
        url=f"https://example.test/{source_id}",
        journal=journal,
        category=category,
        journal_abbreviation=journal_abbreviation,
        journal_nlm_id=journal_nlm_id,
        journal_issns=journal_issns,
        publication_types=publication_types,
    )


def test_parser_applies_boolean_precedence() -> None:
    query = parse_query("alpha OR beta AND NOT gamma")

    assert isinstance(query.root, Or)
    assert query.root.left == Term("alpha")
    assert isinstance(query.root.right, And)
    assert query.root.right.left == Term("beta")
    assert query.root.right.right == Not(Term("gamma"))


def test_parentheses_override_precedence() -> None:
    query = parse_query("(alpha OR beta) AND gamma")

    assert query.matches(paper("alpha gamma"))
    assert query.matches(paper("beta gamma"))
    assert not query.matches(paper("alpha"))


def test_operators_are_case_insensitive() -> None:
    query = parse_query("alpha and not beta")

    assert query.matches(paper("Alpha result"))
    assert not query.matches(paper("Alpha and BETA result"))


def test_terms_default_to_title_or_abstract() -> None:
    query = parse_query("oncology")

    assert query.matches(paper("An oncology atlas"))
    assert query.matches(paper("An atlas", "Applications in oncology"))
    assert not query.matches(paper("An atlas", None))


def test_field_prefixes_restrict_matching() -> None:
    title_query = parse_query("title:cancer")
    abstract_query = parse_query("abstract:cancer")

    candidate = paper("A neutral title", "Cancer cells were measured")
    assert not title_query.matches(candidate)
    assert abstract_query.matches(candidate)
    assert title_query.root == Term("cancer", field=Field.TITLE)


def test_field_prefix_scopes_a_parenthesized_group() -> None:
    query = parse_query(
        'title:("spatial transcriptomics" OR single-cell) AND NOT abstract:review'
    )

    assert query.matches(paper("A single-cell atlas", "Original experiment"))
    assert query.matches(
        paper("Spatial transcriptomics of tissue", "Original experiment")
    )
    assert not query.matches(paper("An atlas", "single-cell measurements"))
    assert not query.matches(paper("A single-cell atlas", "A systematic review"))


def test_nested_field_prefix_overrides_group_scope() -> None:
    query = parse_query("title:(cancer OR abstract:oncology)")

    assert query.matches(paper("A neutral title", "An oncology experiment"))


def test_journal_field_matches_exact_normalized_identity() -> None:
    query = parse_query("journal:Nature")

    assert query.matches(paper("Result", journal="Nature"))
    assert not query.matches(paper("Result", journal="Nature Medicine"))


@pytest.mark.parametrize(
    "attributes",
    [
        {"journal_abbreviation": "NAT"},
        {"journal_nlm_id": "0410462"},
        {"journal_issns": ("1476-4687", "0028-0836")},
    ],
)
def test_journal_field_accepts_retained_pubmed_identifiers(attributes: dict) -> None:
    assert parse_query("journal:1476-4687 OR journal:NAT OR journal:0410462").matches(
        paper("Result", journal="Nature", **attributes)
    )


def test_journal_field_scopes_boolean_groups() -> None:
    query = parse_query("title_abstract:cancer AND journal:(Nature OR Science OR Cell)")

    assert query.matches(paper("Cancer mechanisms", journal="Science"))
    assert not query.matches(paper("Cancer mechanisms", journal="Cancer Cell"))
    assert not query.matches(paper("Plant mechanisms", journal="Science"))


def test_journal_group_field_uses_bundled_collections() -> None:
    query = parse_query("journal_group:flagship_nsc")

    assert query.matches(paper("Result", journal="Cell"))
    assert not query.matches(paper("Result", journal="Cancer Cell"))
    assert parse_query("journal_group:nature_family").matches(
        paper("Result", journal="Nature Medicine")
    )


def test_category_field_matches_biorxiv_categories() -> None:
    query = parse_query('category:"systems biology" AND title:cancer')

    assert query.matches(
        paper(
            "Cancer model",
            source=PaperSource.BIORXIV,
            category="Systems Biology",
        )
    )
    assert not query.matches(
        paper("Cancer model", source=PaperSource.BIORXIV, category="genomics")
    )


def test_publication_type_supports_original_research_filter() -> None:
    query = parse_query("publication_type:original_research")

    assert query.matches(paper("Experiment", publication_types=("Journal Article",)))
    assert query.matches(
        paper(
            "Trial",
            publication_types=("Journal Article", "Randomized Controlled Trial"),
        )
    )
    assert not query.matches(
        paper("Review", publication_types=("Journal Article", "Review"))
    )
    assert not query.matches(paper("News item", publication_types=("News",)))
    assert query.matches(paper("Preprint", source=PaperSource.BIORXIV))


def test_publication_type_can_match_exact_pubmed_type() -> None:
    candidate = paper(
        "Trial",
        publication_types=("Journal Article", "Randomized Controlled Trial"),
    )

    assert parse_query('publication_type:"randomized controlled trial"').matches(
        candidate
    )
    assert not parse_query("publication_type:review").matches(candidate)


def test_quoted_phrases_are_case_insensitive_and_whitespace_normalized() -> None:
    query = parse_query('title:"single cell atlas"')

    assert query.matches(paper("A SINGLE\n cell   atlas of tissue"))
    assert not query.matches(paper("A single-cell atlas of tissue"))


def test_unquoted_terms_use_word_boundaries() -> None:
    query = parse_query("cell")

    assert query.matches(paper("A cell atlas"))
    assert not query.matches(paper("A cellular atlas"))


def test_quoted_boolean_word_is_a_search_phrase() -> None:
    query = parse_query('title:"not"')

    assert query.matches(paper("Why not use this method?"))


def test_quoted_phrase_supports_quote_and_backslash_escapes() -> None:
    query = parse_query(r'title:"a \"quoted\" \\ result"')

    assert query.matches(paper('An a "quoted" \\ result appears'))


def test_root_not_expression_is_supported() -> None:
    query = parse_query("NOT review")

    assert query.matches(paper("An original experiment"))
    assert not query.matches(paper("A review"))


def test_filter_papers_accepts_text_and_preserves_source_and_order() -> None:
    papers = [
        paper(
            "A cancer atlas",
            source=PaperSource.PUBMED,
            source_id="pubmed",
        ),
        paper(
            "A plant atlas",
            source=PaperSource.BIORXIV,
            source_id="plant",
        ),
        paper(
            "A cancer preprint",
            source=PaperSource.BIORXIV,
            source_id="biorxiv",
        ),
    ]

    matches = filter_papers(papers, "title:cancer")

    assert [match.source_id for match in matches] == ["pubmed", "biorxiv"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "query must not be empty"),
        ("   \n  ", "query must not be empty"),
        ("alpha beta", "expected AND or OR"),
        ("alpha AND", "expected a term"),
        ("AND alpha", "expected a term"),
        ("()", "expected a term"),
        ("(alpha OR beta", "expected ')'"),
        ("alpha)", "unexpected token"),
        ("publisher:cancer", "unknown field 'publisher'"),
        ("title:", "expected a term"),
        ('""', "quoted phrase must not be empty"),
        ('"unterminated', "unterminated quoted phrase"),
        (r'"bad\q escape"', "unsupported escape"),
    ],
)
def test_invalid_queries_raise_clear_errors(text: str, message: str) -> None:
    with pytest.raises(QuerySyntaxError, match=re.escape(message)) as error:
        parse_query(text)

    assert error.value.line >= 1
    assert error.value.column >= 1


def test_syntax_error_reports_multiline_location() -> None:
    with pytest.raises(QuerySyntaxError) as error:
        parse_query("alpha AND\nOR beta")

    assert error.value.line == 2
    assert error.value.column == 1
    assert "line 2, column 1" in str(error.value)
