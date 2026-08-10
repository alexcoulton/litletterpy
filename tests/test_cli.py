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
