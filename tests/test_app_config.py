from __future__ import annotations

import json
from pathlib import Path

import pytest

from litletter.app_config import (
    SecretConfig,
    default_app_config_path,
    load_app_config,
    parse_app_config,
)
from litletter.config import parse_config, validate_provider_references
from litletter.errors import ConfigurationError


def payload() -> dict:
    return {
        "version": 1,
        "providers": {
            "paper_sources": {
                "pubmed-default": {
                    "type": "pubmed",
                    "email": "reader@example.com",
                    "api_key_env": "NCBI_API_KEY",
                }
            },
            "summarizers": {
                "deepseek-default": {
                    "type": "deepseek",
                    "api_key": "deepseek-secret",
                    "base_url": "https://api.deepseek.com",
                }
            },
            "mailers": {
                "postmark-default": {
                    "type": "postmark",
                    "server_token_env": "POSTMARK_TOKEN",
                },
                "resend-default": {
                    "type": "resend",
                    "api_key_env": "RESEND_API_KEY",
                },
            },
        },
    }


def test_app_config_resolves_profiles_without_revealing_secrets(tmp_path: Path) -> None:
    config = parse_app_config(payload(), path=tmp_path / "app.json")

    assert config.pubmed("pubmed-default").email == "reader@example.com"
    assert config.summarizer("deepseek-default").api_key.resolve() == (
        "deepseek-secret"
    )
    assert "deepseek-secret" not in repr(config)
    assert (
        config.mailer("postmark-default").server_token.resolve(
            environ={"POSTMARK_TOKEN": "postmark-secret"}
        )
        == "postmark-secret"
    )
    assert (
        config.mailer("resend-default").api_key.resolve(
            environ={"RESEND_API_KEY": "resend-secret"}
        )
        == "resend-secret"
    )


def test_secret_environment_must_exist() -> None:
    secret = SecretConfig(environment="MISSING")

    with pytest.raises(ConfigurationError, match="MISSING"):
        secret.resolve(environ={})
    assert secret.resolve_optional(environ={}) is None
    assert secret.availability(environ={}) == "not set (environment MISSING)"


def test_app_config_rejects_ambiguous_secret(tmp_path: Path) -> None:
    value = payload()
    value["providers"]["summarizers"]["deepseek-default"]["api_key_env"] = (
        "DEEPSEEK_API_KEY"
    )

    with pytest.raises(ConfigurationError, match="cannot contain both"):
        parse_app_config(value, path=tmp_path / "app.json")


def test_default_path_obeys_explicit_and_xdg_configuration(tmp_path: Path) -> None:
    assert default_app_config_path(
        environ={"LITLETTER_APP_CONFIG": "/etc/litletter/app.json"},
        home=tmp_path,
    ) == Path("/etc/litletter/app.json")
    assert default_app_config_path(
        environ={"XDG_CONFIG_HOME": "/tmp/config"}, home=tmp_path
    ) == Path("/tmp/config/litletter/app.json")


def test_literal_secrets_require_private_file_permissions(tmp_path: Path) -> None:
    path = tmp_path / "app.json"
    path.write_text(json.dumps(payload()), encoding="utf-8")
    path.chmod(0o644)

    with pytest.raises(ConfigurationError, match="chmod 600"):
        load_app_config(path)


def test_disabled_provider_validation_allows_one_run_bypass(tmp_path: Path) -> None:
    newsletter_payload = {
        "version": 2,
        "database": "state.sqlite3",
        "newsletter": {
            "title": "Litletter",
            "from": "sender@example.com",
            "to": ["reader@example.com"],
            "timezone": "UTC",
        },
        "sources": {
            "pubmed": {"enabled": True, "provider": "pubmed-default"},
            "biorxiv": {"enabled": False},
        },
        "discovery": {"initial_lookback_days": 30, "overlap_days": 2},
        "categories": [
            {
                "id": "cancer",
                "name": "Cancer",
                "query": "cancer",
                "sources": ["pubmed"],
            }
        ],
        "summarization": {
            "enabled": True,
            "provider": "missing-deepseek",
        },
        "delivery": {
            "provider": "postmark-default",
            "message_stream": "broadcasts",
        },
    }
    newsletter = parse_config(newsletter_payload, path=tmp_path / "newsletter.json")
    app = parse_app_config(payload(), path=tmp_path / "app.json")

    with pytest.raises(ConfigurationError, match="missing-deepseek"):
        validate_provider_references(newsletter, app)

    validate_provider_references(newsletter, app, include_summarizer=False)


def test_delivery_settings_are_validated_for_selected_mailer(tmp_path: Path) -> None:
    newsletter_payload = {
        "version": 2,
        "database": "state.sqlite3",
        "newsletter": {
            "title": "Litletter",
            "from": "sender@example.com",
            "to": ["reader@example.com"],
            "timezone": "UTC",
        },
        "sources": {
            "pubmed": {"enabled": True, "provider": "pubmed-default"},
            "biorxiv": {"enabled": False},
        },
        "discovery": {"initial_lookback_days": 30, "overlap_days": 2},
        "categories": [
            {
                "id": "cancer",
                "name": "Cancer",
                "query": "cancer",
                "sources": ["pubmed"],
            }
        ],
        "summarization": {"enabled": False},
        "delivery": {"provider": "resend-default"},
    }
    app = parse_app_config(payload(), path=tmp_path / "app.json")
    newsletter = parse_config(newsletter_payload, path=tmp_path / "newsletter.json")

    validate_provider_references(newsletter, app)

    newsletter_payload["delivery"]["message_stream"] = "broadcasts"
    newsletter = parse_config(newsletter_payload, path=tmp_path / "newsletter.json")
    with pytest.raises(ConfigurationError, match="only valid for a Postmark"):
        validate_provider_references(newsletter, app)

    newsletter_payload["delivery"] = {"provider": "postmark-default"}
    newsletter = parse_config(newsletter_payload, path=tmp_path / "newsletter.json")
    with pytest.raises(ConfigurationError, match="required for a Postmark"):
        validate_provider_references(newsletter, app)
