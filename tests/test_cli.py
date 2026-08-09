from __future__ import annotations

import json
from pathlib import Path

from litletter.cli import main


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "litletter.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
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
                        "email": "reader@example.com",
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
                "delivery": {
                    "provider": "postmark",
                    "token_env": "POSTMARK_TOKEN",
                    "message_stream": "broadcasts",
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_validate_initialize_and_report_status(tmp_path: Path, capsys) -> None:
    config = write_config(tmp_path)

    assert main(["config", "validate", "--config", str(config)]) == 0
    assert "Configuration is valid" in capsys.readouterr().out

    assert main(["db", "init", "--config", str(config)]) == 0
    assert (tmp_path / "state" / "litletter.sqlite3").exists()
    assert "Database initialized" in capsys.readouterr().out

    assert main(["status", "--config", str(config)]) == 0
    output = capsys.readouterr().out
    assert "Papers: 0" in output
    assert "Last successful date: never" in output
    assert "Open edition: none" in output


def test_cli_reports_configuration_errors(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing.json"

    assert main(["config", "validate", "--config", str(missing)]) == 1

    assert "could not read config" in capsys.readouterr().err
