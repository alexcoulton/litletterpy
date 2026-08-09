from datetime import date

import pytest

from litletter.models import Author, Paper, PaperSource


def test_models_are_immutable_and_validate_required_text() -> None:
    author = Author(name="Ada Lovelace")
    paper = Paper(
        source=PaperSource.PUBMED,
        source_id="123",
        title="A paper",
        abstract=None,
        authors=(author,),
        published_at=date(2026, 8, 9),
        updated_at=None,
        doi=None,
        url="https://example.test/123",
    )

    assert paper.authors == (author,)
    with pytest.raises(AttributeError):
        paper.title = "Changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="author name"):
        Author(name=" ")
