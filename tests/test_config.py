from __future__ import annotations

from pathlib import Path

import pytest

from litletter.config import parse_config
from litletter.errors import ConfigurationError
from litletter.models import PaperSource


def config_payload() -> dict:
    return {
        "version": 2,
        "database": "state/litletter.sqlite3",
        "newsletter": {
            "title": "My Litletter",
            "from": "sender@example.com",
            "to": ["reader@example.com"],
            "timezone": "Europe/London",
        },
        "sources": {
            "pubmed": {
                "enabled": True,
                "provider": "pubmed-default",
            },
            "biorxiv": {"enabled": True},
            "medrxiv": {"enabled": False},
            "arxiv": {"enabled": False},
        },
        "discovery": {"initial_lookback_days": 30, "overlap_days": 2},
        "categories": [
            {
                "id": "nsc-cancer",
                "name": "NSC Cancer",
                "query": ("title_abstract:cancer AND journal_group:flagship_nsc"),
                "sources": ["pubmed"],
            }
        ],
        "summarization": {"enabled": False},
        "delivery": {
            "provider": "postmark-default",
            "message_stream": "broadcasts",
        },
    }


def test_parse_config_validates_and_resolves_paths(tmp_path: Path) -> None:
    path = tmp_path / "litletter.json"

    config = parse_config(config_payload(), path=path)

    assert config.database == tmp_path / "state" / "litletter.sqlite3"
    assert config.newsletter.abstract_max_characters == 800
    assert config.newsletter.include_abstracts is False
    assert config.pubmed.provider == "pubmed-default"
    assert config.biorxiv.enabled is True
    assert config.medrxiv.enabled is False
    assert config.arxiv.enabled is False
    assert config.summarization.enabled is False
    assert config.categories[0].id == "nsc-cancer"
    assert config.categories[0].sources == (PaperSource.PUBMED,)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(version=1), "version must be 2"),
        (
            lambda value: value["categories"][0].update(id="NSC Cancer"),
            "id must contain lowercase",
        ),
        (
            lambda value: value["categories"][0].update(query="cancer words"),
            "query is invalid",
        ),
        (
            lambda value: value["newsletter"].update(timezone="Moon/Base"),
            "timezone is unknown",
        ),
        (
            lambda value: value["newsletter"].update(include_abstracts="no"),
            "include_abstracts must be a boolean",
        ),
        (
            lambda value: value.update(unexpected=True),
            "unknown keys: unexpected",
        ),
    ],
)
def test_parse_config_rejects_invalid_values(mutate, message: str) -> None:
    payload = config_payload()
    mutate(payload)

    with pytest.raises(ConfigurationError, match=message):
        parse_config(payload, path=Path("/tmp/litletter.json"))


@pytest.mark.parametrize("source", ["pubmed", "biorxiv", "medrxiv", "arxiv"])
def test_category_cannot_use_disabled_source(source: str) -> None:
    payload = config_payload()
    payload["sources"][source]["enabled"] = False
    payload["categories"][0]["sources"] = [source]

    with pytest.raises(ConfigurationError, match="uses disabled source"):
        parse_config(payload, path=Path("/tmp/litletter.json"))


def test_medrxiv_and_arxiv_categories_are_supported_when_enabled() -> None:
    payload = config_payload()
    payload["sources"]["medrxiv"]["enabled"] = True
    payload["sources"]["arxiv"]["enabled"] = True
    payload["categories"][0]["sources"] = ["medrxiv", "arxiv"]

    config = parse_config(payload, path=Path("/tmp/litletter.json"))

    assert config.categories[0].sources == (
        PaperSource.MEDRXIV,
        PaperSource.ARXIV,
    )


def test_config_loads_relative_author_groups_and_fingerprints_query(
    tmp_path: Path,
) -> None:
    groups = tmp_path / "author_groups.json"
    groups.write_text(
        '{"version":1,"groups":{"watchlist":{"authors":["Alex Coulton"]}}}',
        encoding="utf-8",
    )
    payload = config_payload()
    payload["author_groups"] = "author_groups.json"
    payload["categories"][0]["query"] = "author_group:watchlist"

    config = parse_config(payload, path=tmp_path / "litletter.json")

    assert config.author_catalog is not None
    assert config.author_catalog.path == groups
    assert config.categories[0].query_fingerprint is not None
    assert "# author-groups:" in config.categories[0].stored_query


def test_config_rejects_unknown_author_group(tmp_path: Path) -> None:
    groups = tmp_path / "author_groups.json"
    groups.write_text(
        '{"version":1,"groups":{"watchlist":{"authors":["Alex Coulton"]}}}',
        encoding="utf-8",
    )
    payload = config_payload()
    payload["author_groups"] = "author_groups.json"
    payload["categories"][0]["query"] = "author_group:missing"

    with pytest.raises(ConfigurationError, match="unknown author group 'missing'"):
        parse_config(payload, path=tmp_path / "litletter.json")


def test_config_loads_builtin_author_collection() -> None:
    payload = config_payload()
    payload["author_groups"] = "builtin:cancer_researchers"
    payload["categories"][0]["query"] = "author_group:cancer-watchlist"

    config = parse_config(payload, path=Path("/tmp/litletter.json"))

    assert config.author_catalog is not None
    assert len(config.author_catalog.get("cancer-watchlist").authors) == 205
