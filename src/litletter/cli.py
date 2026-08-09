"""Command-line interface for scheduled Litletter runs."""

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from litletter.config import LitletterConfig, load_config
from litletter.delivery import PostmarkMailer
from litletter.errors import ConfigurationError, DatabaseError, LitletterError
from litletter.models import PaperSource
from litletter.runner import RunResult, run_once
from litletter.sources import BioRxivClient, PubMedClient
from litletter.storage import Database

_LOGGER = logging.getLogger(__name__)
_DEFAULT_CONFIG = "litletter.json"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return int(args.handler(args))
    except (LitletterError, OSError, ValueError) as exc:
        _LOGGER.debug("Command failed", exc_info=True)
        print(f"litletter: error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litletter",
        description="Discover papers and send a categorized literature newsletter.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    config_command = commands.add_parser("config", help="configuration commands")
    config_commands = config_command.add_subparsers(required=True)
    validate = config_commands.add_parser("validate", help="validate JSON config")
    _add_config_argument(validate)
    validate.set_defaults(handler=_validate_config)

    database_command = commands.add_parser("db", help="database commands")
    database_commands = database_command.add_subparsers(required=True)
    initialize = database_commands.add_parser("init", help="initialize the database")
    _add_config_argument(initialize)
    initialize.set_defaults(handler=_initialize_database)

    run = commands.add_parser("run", help="perform one discovery and delivery run")
    _add_config_argument(run)
    run.add_argument(
        "--bootstrap",
        action="store_true",
        help="approve the configured initial lookback window",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and render without creating or sending an edition",
    )
    run.add_argument(
        "--retry-open-edition",
        action="store_true",
        help="retry a failed/draft edition after confirming it was not delivered",
    )
    run.add_argument(
        "--output",
        type=Path,
        help="write the rendered HTML preview to this path",
    )
    run.add_argument(
        "--date",
        type=date.fromisoformat,
        help="override today's local date (YYYY-MM-DD; useful for testing)",
    )
    run.add_argument("--verbose", action="store_true", help="enable debug logging")
    run.set_defaults(handler=_run)

    status = commands.add_parser("status", help="show durable newsletter state")
    _add_config_argument(status)
    status.set_defaults(handler=_status)

    edition = commands.add_parser("edition", help="edition recovery commands")
    edition_commands = edition.add_subparsers(required=True)
    resolve = edition_commands.add_parser(
        "resolve", help="resolve a delivery left in the uncertain sending state"
    )
    _add_config_argument(resolve)
    resolve.add_argument("edition_id", help="edition ID shown by litletter status")
    outcome = resolve.add_mutually_exclusive_group(required=True)
    outcome.add_argument(
        "--delivered",
        action="store_true",
        help="mark submitted after finding the message in Postmark",
    )
    outcome.add_argument(
        "--not-delivered",
        action="store_true",
        help="mark failed after confirming Postmark did not accept it",
    )
    resolve.add_argument(
        "--message-id", help="Postmark message ID (required with --delivered)"
    )
    resolve.set_defaults(handler=_resolve_edition)
    return parser


def _add_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("LITLETTER_CONFIG", _DEFAULT_CONFIG)),
        help="JSON configuration path (default: LITLETTER_CONFIG or litletter.json)",
    )


def _validate_config(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(f"Configuration is valid: {config.path}")
    print(f"Categories: {len(config.categories)}")
    print(f"Database: {config.database}")
    return 0


def _initialize_database(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    database = Database(config.database)
    try:
        database.initialize()
        database.sync_categories(config.categories)
    finally:
        database.close()
    print(f"Database initialized: {config.database}")
    return 0


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with (
        _exclusive_run_lock(config.database),
        Database(config.database) as database,
        ExitStack() as stack,
    ):
        pubmed, biorxiv = _create_sources(config, stack)
        mailer = None
        if not args.dry_run:
            token = os.environ.get(config.delivery.token_env)
            if token is None or not token.strip():
                raise ConfigurationError(
                    f"environment variable {config.delivery.token_env!r} "
                    "must contain the Postmark server token"
                )
            mailer = stack.enter_context(
                PostmarkMailer(
                    server_token=token,
                    from_address=config.newsletter.from_address,
                    to=config.newsletter.to,
                    message_stream=config.delivery.message_stream,
                )
            )
        today = args.date or datetime.now(ZoneInfo(config.newsletter.timezone)).date()
        result = run_once(
            config,
            database,
            pubmed=pubmed,
            biorxiv=biorxiv,
            mailer=mailer,
            today=today,
            bootstrap=args.bootstrap,
            dry_run=args.dry_run,
            retry_open_edition=args.retry_open_edition,
        )
    _print_run_result(result)
    if args.output is not None:
        if result.newsletter is None:
            raise ValueError("there is no newsletter to write")
        output = args.output.expanduser()
        output.write_text(result.newsletter.html, encoding="utf-8")
        print(f"HTML written to {output}")
    elif args.dry_run and result.newsletter is not None:
        print("\n" + result.newsletter.text)
    return 0


def _create_sources(
    config: LitletterConfig, stack: ExitStack
) -> tuple[PubMedClient | None, BioRxivClient | None]:
    configured_sources = {
        source for category in config.categories for source in category.sources
    }
    pubmed = None
    if PaperSource.PUBMED in configured_sources:
        api_key = (
            os.environ.get(config.pubmed.api_key_env)
            if config.pubmed.api_key_env
            else None
        )
        pubmed = stack.enter_context(
            PubMedClient(email=config.pubmed.email, api_key=api_key)
        )
    biorxiv = None
    if PaperSource.BIORXIV in configured_sources:
        biorxiv = stack.enter_context(BioRxivClient())
    return pubmed, biorxiv


def _status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with Database(config.database) as database:
        state = database.status()
    print(f"Database: {config.database}")
    print(f"Papers: {state.papers}")
    print(f"Category matches: {state.category_matches}")
    print(f"Unsent papers: {state.unsent_papers}")
    print(f"Submitted editions: {state.submitted_editions}")
    print(f"Last successful date: {state.last_successful_until or 'never'}")
    if state.open_edition is None:
        print("Open edition: none")
    else:
        open_edition = state.open_edition
        print(
            f"Open edition: {open_edition.id} "
            f"({open_edition.status}, {open_edition.item_count} papers)"
        )
        if open_edition.error:
            print(f"Edition error: {open_edition.error}")
    return 0


def _resolve_edition(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.delivered and not args.message_id:
        raise ValueError("--message-id is required with --delivered")
    if args.not_delivered and args.message_id:
        raise ValueError("--message-id is only valid with --delivered")
    with (
        _exclusive_run_lock(config.database),
        Database(config.database) as database,
    ):
        database.resolve_uncertain_delivery(
            args.edition_id,
            delivered=args.delivered,
            message_id=args.message_id,
        )
    outcome = "submitted" if args.delivered else "failed and available for retry"
    print(f"Edition {args.edition_id} marked {outcome}")
    return 0


def _print_run_result(result: RunResult) -> None:
    if result.since is not None and result.until is not None:
        print(f"Discovery window: {result.since} to {result.until}")
    print(f"Category matches processed: {result.matched}")
    print(f"Unsent papers selected: {result.unsent}")
    print(result.message)
    if result.receipt is not None:
        print(
            f"Provider: {result.receipt.provider}; "
            f"message ID: {result.receipt.message_id}"
        )


@contextmanager
def _exclusive_run_lock(database_path: Path) -> Iterator[None]:
    """Prevent overlapping cron invocations for one database."""
    lock_path = database_path.with_name(database_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatabaseError(
                f"another Litletter run holds the lock: {lock_path}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
