"""Command-line interface for scheduled Litletter runs."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from litletter.app_config import (
    AppConfig,
    PostmarkProviderConfig,
    ResendProviderConfig,
    app_config_template,
    default_app_config_path,
    load_app_config,
)
from litletter.author_groups import author_catalog_template
from litletter.config import (
    LitletterConfig,
    load_config,
    newsletter_config_template,
    validate_provider_references,
)
from litletter.delivery import Mailer, PostmarkMailer, ResendMailer
from litletter.errors import ConfigurationError, DatabaseError, LitletterError
from litletter.models import PaperSource
from litletter.runner import RunResult, enrich_pending_papers, run_once
from litletter.sources import ArXivClient, BioRxivClient, MedRxivClient, PubMedClient
from litletter.storage import Database
from litletter.summarization import DeepSeekSummarizer

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

    initialize_all = commands.add_parser(
        "init", help="create a starter newsletter and provider configuration"
    )
    _add_config_argument(initialize_all)
    initialize_all.set_defaults(handler=_initialize)

    app_command = commands.add_parser("app-config", help="global provider config")
    app_commands = app_command.add_subparsers(required=True)
    app_init = app_commands.add_parser("init", help="create a global config template")
    _add_app_config_argument(app_init)
    app_init.set_defaults(handler=_initialize_app_config)
    app_validate = app_commands.add_parser("validate", help="validate global config")
    _add_app_config_argument(app_validate)
    app_validate.set_defaults(handler=_validate_app_config)
    app_path = app_commands.add_parser("path", help="print the global config path")
    _add_app_config_argument(app_path)
    app_path.set_defaults(handler=_show_app_config_path)

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
        "--no-summarization",
        action="store_true",
        help="render this run with original abstracts even when configured",
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

    summarize = commands.add_parser(
        "summarize", help="precompute summaries without discovery or delivery"
    )
    _add_config_argument(summarize)
    summarize.add_argument(
        "--pending", action="store_true", required=True, help="summarize unsent papers"
    )
    summarize.add_argument(
        "--verbose", action="store_true", help="enable debug logging"
    )
    summarize.set_defaults(handler=_summarize)

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
        help="mark submitted after finding the message at the email provider",
    )
    outcome.add_argument(
        "--not-delivered",
        action="store_true",
        help="mark failed after confirming the email provider did not accept it",
    )
    resolve.add_argument(
        "--message-id", help="provider message ID (required with --delivered)"
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
    _add_app_config_argument(parser)


def _add_app_config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--app-config",
        type=Path,
        default=default_app_config_path(),
        help="global provider config (default: LITLETTER_APP_CONFIG or XDG path)",
    )


def _load_configs(
    args: argparse.Namespace,
    *,
    include_summarizer: bool = True,
    include_mailer: bool = True,
) -> tuple[LitletterConfig, AppConfig]:
    config = load_config(args.config)
    app_config = load_app_config(args.app_config)
    validate_provider_references(
        config,
        app_config,
        include_summarizer=include_summarizer,
        include_mailer=include_mailer,
    )
    return config, app_config


def _initialize_app_config(args: argparse.Namespace) -> int:
    path = args.app_config.expanduser().resolve()
    _write_json_exclusive(path, app_config_template(), mode=0o600, kind="app config")
    print(f"App config created with mode 0600: {path}")
    return 0


def _initialize(args: argparse.Namespace) -> int:
    config_path = args.config.expanduser().resolve()
    app_path = args.app_config.expanduser().resolve()
    if config_path.exists():
        raise ConfigurationError(f"newsletter config already exists: {config_path}")
    database_path = config_path.parent / "state" / "litletter.sqlite3"
    author_groups_path = config_path.parent / "author_groups.json"
    if database_path.exists():
        raise ConfigurationError(f"starter database already exists: {database_path}")
    if author_groups_path.exists():
        raise ConfigurationError(
            f"starter author groups already exist: {author_groups_path}"
        )

    if app_path.exists():
        app_config = load_app_config(app_path)
        app_message = f"Using existing app config: {app_path}"
    else:
        _write_json_exclusive(
            app_path, app_config_template(), mode=0o600, kind="app config"
        )
        app_config = load_app_config(app_path)
        app_message = f"Created private app config: {app_path}"

    if not app_config.paper_sources:
        raise ConfigurationError("app config has no paper source profiles")
    if not app_config.mailers:
        raise ConfigurationError("app config has no mailer profiles")
    pubmed_provider = app_config.paper_sources[0].id
    mailer = next(
        (
            profile
            for profile in app_config.mailers
            if isinstance(profile, ResendProviderConfig)
        ),
        app_config.mailers[0],
    )
    _write_json_exclusive(
        author_groups_path,
        author_catalog_template(),
        mode=0o644,
        kind="author groups",
    )
    _write_json_exclusive(
        config_path,
        newsletter_config_template(
            pubmed_provider=pubmed_provider,
            mailer_provider=mailer.id,
            message_stream=(
                "broadcasts" if isinstance(mailer, PostmarkProviderConfig) else None
            ),
        ),
        mode=0o644,
        kind="newsletter config",
    )
    config = load_config(config_path)
    validate_provider_references(config, app_config)
    database = Database(config.database)
    try:
        database.initialize()
        database.sync_categories(config.categories)
    finally:
        database.close()

    print(f"Created newsletter config: {config_path}")
    print(f"Created author groups: {author_groups_path}")
    print(app_message)
    print(f"Initialized database: {config.database}")
    credential = (
        "Resend API key"
        if isinstance(mailer, ResendProviderConfig)
        else "Postmark server token and message stream"
    )
    print(f"\nNext: edit the email addresses and {credential}, then preview with:")
    print("litletter run --bootstrap --dry-run --output litletter-preview.html")
    return 0


def _write_json_exclusive(
    path: Path,
    payload: object,
    *,
    mode: int,
    kind: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise ConfigurationError(f"{kind} already exists: {path}") from exc


def _validate_app_config(args: argparse.Namespace) -> int:
    app_config = load_app_config(args.app_config)
    print(f"App configuration is valid: {app_config.path}")
    print(f"Paper source profiles: {len(app_config.paper_sources)}")
    print(f"Summarizer profiles: {len(app_config.summarizers)}")
    print(f"Mailer profiles: {len(app_config.mailers)}")
    for profile in app_config.paper_sources:
        status = (
            profile.api_key.availability()
            if profile.api_key
            else "not configured (optional)"
        )
        print(f"PubMed {profile.id} API key: {status}")
    for profile in app_config.summarizers:
        print(f"DeepSeek {profile.id} API key: {profile.api_key.availability()}")
    for profile in app_config.mailers:
        if isinstance(profile, PostmarkProviderConfig):
            print(
                f"Postmark {profile.id} server token: "
                f"{profile.server_token.availability()}"
            )
        elif isinstance(profile, ResendProviderConfig):
            print(f"Resend {profile.id} API key: {profile.api_key.availability()}")
    return 0


def _show_app_config_path(args: argparse.Namespace) -> int:
    print(args.app_config.expanduser().resolve())
    return 0


def _validate_config(args: argparse.Namespace) -> int:
    config, app_config = _load_configs(args)
    print(f"Configuration is valid: {config.path}")
    print(f"App configuration: {app_config.path}")
    print(f"Categories: {len(config.categories)}")
    print(f"Database: {config.database}")
    return 0


def _initialize_database(args: argparse.Namespace) -> int:
    config, _ = _load_configs(args)
    database = Database(config.database)
    try:
        database.initialize()
        database.sync_categories(config.categories)
    finally:
        database.close()
    print(f"Database initialized: {config.database}")
    return 0


def _run(args: argparse.Namespace) -> int:
    config, app_config = _load_configs(
        args,
        include_summarizer=not args.no_summarization,
        include_mailer=not args.dry_run,
    )
    with (
        _exclusive_run_lock(config.database),
        Database(config.database) as database,
        ExitStack() as stack,
    ):
        pubmed, biorxiv, medrxiv, arxiv = _create_sources(config, app_config, stack)
        summarizer = (
            None
            if args.no_summarization or not config.summarization.enabled
            else _create_summarizer(config, app_config, stack)
        )
        mailer = None
        if not args.dry_run:
            mailer = _create_mailer(config, app_config, stack)
        today = args.date or datetime.now(ZoneInfo(config.newsletter.timezone)).date()
        result = run_once(
            config,
            database,
            pubmed=pubmed,
            biorxiv=biorxiv,
            medrxiv=medrxiv,
            arxiv=arxiv,
            mailer=mailer,
            summarizer=summarizer,
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
    config: LitletterConfig, app_config: AppConfig, stack: ExitStack
) -> tuple[
    PubMedClient | None,
    BioRxivClient | None,
    MedRxivClient | None,
    ArXivClient | None,
]:
    configured_sources = {
        source for category in config.categories for source in category.sources
    }
    pubmed = None
    if PaperSource.PUBMED in configured_sources:
        provider = app_config.pubmed(config.pubmed.provider or "")
        api_key = provider.api_key.resolve_optional() if provider.api_key else None
        pubmed = stack.enter_context(
            PubMedClient(email=provider.email, api_key=api_key)
        )
    biorxiv = None
    if PaperSource.BIORXIV in configured_sources:
        biorxiv = stack.enter_context(BioRxivClient())
    medrxiv = None
    if PaperSource.MEDRXIV in configured_sources:
        medrxiv = stack.enter_context(MedRxivClient())
    arxiv = None
    if PaperSource.ARXIV in configured_sources:
        arxiv = stack.enter_context(ArXivClient())
    return pubmed, biorxiv, medrxiv, arxiv


def _create_summarizer(
    config: LitletterConfig, app_config: AppConfig, stack: ExitStack
) -> DeepSeekSummarizer:
    settings = config.summarization
    provider = app_config.summarizer(settings.provider or "")
    return stack.enter_context(
        DeepSeekSummarizer(
            profile_id=provider.id,
            api_key=provider.api_key.resolve(),
            base_url=provider.base_url,
            model=settings.model,
            audience=settings.audience,
            max_words=settings.max_words,
            timeout=provider.timeout_seconds,
        )
    )


def _create_mailer(
    config: LitletterConfig, app_config: AppConfig, stack: ExitStack
) -> Mailer:
    provider = app_config.mailer(config.delivery.provider)
    if isinstance(provider, PostmarkProviderConfig):
        if config.delivery.message_stream is None:
            raise ConfigurationError(
                "delivery.message_stream is required for a Postmark provider"
            )
        return stack.enter_context(
            PostmarkMailer(
                server_token=provider.server_token.resolve(),
                from_address=config.newsletter.from_address,
                to=config.newsletter.to,
                message_stream=config.delivery.message_stream,
            )
        )
    return stack.enter_context(
        ResendMailer(
            api_key=provider.api_key.resolve(),
            from_address=config.newsletter.from_address,
            to=config.newsletter.to,
        )
    )


def _status(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with Database(config.database) as database:
        state = database.status()
    print(f"Database: {config.database}")
    print(f"Papers: {state.papers}")
    print(f"Category matches: {state.category_matches}")
    print(f"Unsent papers: {state.unsent_papers}")
    print(f"Cached summaries: {state.cached_summaries}")
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
    print(f"Summaries created: {result.summaries_created}")
    print(f"Summaries reused from cache: {result.summaries_cached}")
    print(f"Summary fallbacks: {result.summary_failures}")
    print(result.message)
    if result.receipt is not None:
        print(
            f"Provider: {result.receipt.provider}; "
            f"message ID: {result.receipt.message_id}"
        )


def _summarize(args: argparse.Namespace) -> int:
    config, app_config = _load_configs(args)
    if not config.summarization.enabled:
        print("Summarization is disabled in the newsletter config")
        return 0
    with (
        _exclusive_run_lock(config.database),
        Database(config.database) as database,
        ExitStack() as stack,
    ):
        database.sync_categories(config.categories)
        summarizer = _create_summarizer(config, app_config, stack)
        items = database.unsent_papers()
        _, stats = enrich_pending_papers(
            database,
            items,
            summarizer=summarizer,
            failure_policy=config.summarization.failure_policy,
        )
    print(f"Pending papers: {len(items)}")
    print(f"Summaries created: {stats.created}")
    print(f"Summaries reused from cache: {stats.cached}")
    print(f"Summary fallbacks: {stats.failed}")
    print(f"Papers without abstracts: {stats.no_abstract}")
    return 0


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
