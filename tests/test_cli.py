from __future__ import annotations

import json
from pathlib import Path

from litletter.cli import main


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "litletter.json"
    path.write_text(
        json.dumps(
            {
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
                    "biorxiv": {"enabled": False},
                },
                "discovery": {
                    "initial_lookback_days": 30,
                    "overlap_days": 2,
                },
                "categories": [
                    {
                        "id": "cancer",
                        "name": "Cancer",
                        "query": "title_abstract:cancer",
                        "sources": ["pubmed"],
                    }
                ],
                "summarization": {"enabled": False},
                "delivery": {
                    "provider": "postmark-default",
                    "message_stream": "broadcasts",
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def write_app_config(tmp_path: Path) -> Path:
    path = tmp_path / "app.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {
                    "paper_sources": {
                        "pubmed-default": {
                            "type": "pubmed",
                            "email": "reader@example.com",
                        }
                    },
                    "summarizers": {},
                    "mailers": {
                        "postmark-default": {
                            "type": "postmark",
                            "server_token": "token",
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_validate_initialize_and_report_status(tmp_path: Path, capsys) -> None:
    config = write_config(tmp_path)
    app_config = write_app_config(tmp_path)
    arguments = ["--config", str(config), "--app-config", str(app_config)]

    assert main(["config", "validate", *arguments]) == 0
    assert "Configuration is valid" in capsys.readouterr().out

    assert main(["db", "init", *arguments]) == 0
    assert (tmp_path / "state" / "litletter.sqlite3").exists()
    assert "Database initialized" in capsys.readouterr().out

    assert main(["status", *arguments]) == 0
    output = capsys.readouterr().out
    assert "Papers: 0" in output
    assert "Last successful date: never" in output
    assert "Open edition: none" in output

    assert main(["summarize", *arguments, "--pending"]) == 0
    assert "Summarization is disabled" in capsys.readouterr().out


def test_cli_reports_configuration_errors(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing.json"

    assert main(["config", "validate", "--config", str(missing)]) == 1

    assert "could not read config" in capsys.readouterr().err


def test_app_config_init_creates_private_template(tmp_path: Path, capsys) -> None:
    path = tmp_path / "config" / "app.json"

    assert main(["app-config", "init", "--app-config", str(path)]) == 0

    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert "mode 0600" in capsys.readouterr().out


def test_init_creates_complete_starter_without_overwriting(
    tmp_path: Path, capsys
) -> None:
    config = tmp_path / "newsletter" / "litletter.json"
    app_config = tmp_path / "config" / "app.json"
    arguments = ["--config", str(config), "--app-config", str(app_config)]

    assert main(["init", "--non-interactive", *arguments]) == 0

    newsletter = json.loads(config.read_text())
    assert newsletter["delivery"] == {"provider": "resend-default"}
    assert newsletter["author_groups"] == "author_groups.json"
    assert (
        "publication_type:original_research" in (newsletter["categories"][0]["query"])
    )
    assert newsletter["newsletter"]["include_abstracts"] is False
    assert newsletter["sources"]["medrxiv"] == {"enabled": False}
    assert newsletter["sources"]["arxiv"] == {"enabled": False}
    assert app_config.stat().st_mode & 0o777 == 0o600
    app = json.loads(app_config.read_text())
    assert app["providers"]["mailers"]["resend-default"] == {
        "type": "resend",
        "api_key": "re_your_api_key",
    }
    assert (config.parent / "state" / "litletter.sqlite3").exists()
    author_groups = config.parent / "author_groups.json"
    assert json.loads(author_groups.read_text())["groups"]["watchlist"]["authors"]
    assert "Next: edit the email addresses" in capsys.readouterr().out

    original = config.read_text()
    assert main(["init", "--non-interactive", *arguments]) == 1
    assert config.read_text() == original
    assert "already exists" in capsys.readouterr().err


def test_init_reuses_valid_existing_app_config(tmp_path: Path, capsys) -> None:
    app_config = write_app_config(tmp_path)
    config = tmp_path / "new" / "litletter.json"

    assert (
        main(
            [
                "init",
                "--non-interactive",
                "--config",
                str(config),
                "--app-config",
                str(app_config),
            ]
        )
        == 0
    )

    assert f"Using existing app config: {app_config}" in capsys.readouterr().out
    newsletter = json.loads(config.read_text())
    assert newsletter["delivery"] == {
        "provider": "postmark-default",
        "message_stream": "broadcasts",
    }


def test_interactive_init_collects_addresses_credentials_and_categories(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    config = tmp_path / "newsletter" / "litletter.json"
    app_config = tmp_path / "config" / "app.json"
    answers = iter(
        [
            "Cancer Watch",
            "reader@example.com",
            "",
            "Europe/London",
            "",
            "Cancer evolution",
            "title_abstract:'cancer evolution' AND publication_type:original_research",
            "pubmed, biorxiv",
            "n",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(
        "litletter.cli.getpass.getpass", lambda _prompt: "re_test_secret"
    )

    assert (
        main(
            [
                "init",
                "--interactive",
                "--config",
                str(config),
                "--app-config",
                str(app_config),
            ]
        )
        == 0
    )

    newsletter = json.loads(config.read_text())
    assert newsletter["newsletter"] == {
        "title": "Cancer Watch",
        "from": "Litletter <onboarding@resend.dev>",
        "to": ["reader@example.com"],
        "timezone": "Europe/London",
        "include_abstracts": False,
        "abstract_max_characters": 800,
    }
    assert newsletter["categories"] == [
        {
            "id": "cancer-evolution",
            "name": "Cancer evolution",
            "query": (
                "title_abstract:'cancer evolution' AND "
                "publication_type:original_research"
            ),
            "sources": ["pubmed", "biorxiv"],
        }
    ]
    assert newsletter["sources"]["pubmed"]["enabled"] is True
    assert newsletter["sources"]["biorxiv"]["enabled"] is True
    assert newsletter["sources"]["medrxiv"]["enabled"] is False
    app = json.loads(app_config.read_text())
    assert app["providers"]["paper_sources"]["pubmed-default"]["email"] == (
        "reader@example.com"
    )
    assert app["providers"]["mailers"]["resend-default"]["api_key"] == (
        "re_test_secret"
    )
    output = capsys.readouterr().out
    assert "Setup complete" in output
    assert "re_test_secret" not in output


def test_interactive_init_cancellation_does_not_create_files(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    config = tmp_path / "newsletter" / "litletter.json"
    app_config = tmp_path / "config" / "app.json"

    def cancel(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", cancel)

    assert (
        main(
            [
                "init",
                "--interactive",
                "--config",
                str(config),
                "--app-config",
                str(app_config),
            ]
        )
        == 1
    )
    assert not config.exists()
    assert not app_config.exists()
    assert "setup cancelled" in capsys.readouterr().err


def test_init_refuses_existing_database_before_creating_files(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "state" / "litletter.sqlite3"
    database.parent.mkdir()
    database.write_text("existing")
    app_config = tmp_path / "private" / "app.json"

    assert (
        main(
            [
                "init",
                "--config",
                str(tmp_path / "litletter.json"),
                "--app-config",
                str(app_config),
            ]
        )
        == 1
    )

    assert not app_config.exists()
    assert "starter database already exists" in capsys.readouterr().err
