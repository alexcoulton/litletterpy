"""SQLite persistence for recurring Litletter discovery and delivery."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from litletter.config import CategoryConfig
from litletter.errors import DatabaseError
from litletter.models import Author, Paper, PaperSource
from litletter.summarization import PaperSummary, SummaryResult

_SCHEMA_VERSION = 2
_SCHEMA = """
CREATE TABLE runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    since_date TEXT NOT NULL,
    until_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    error TEXT
);

CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    query TEXT NOT NULL,
    position INTEGER NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    updated_at TEXT NOT NULL
);

CREATE TABLE papers (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT,
    authors_json TEXT NOT NULL,
    published_at TEXT,
    updated_at TEXT,
    doi TEXT,
    url TEXT NOT NULL,
    journal TEXT,
    category TEXT,
    version INTEGER,
    journal_abbreviation TEXT,
    journal_nlm_id TEXT,
    journal_issns_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);

CREATE TABLE paper_categories (
    paper_source TEXT NOT NULL,
    paper_source_id TEXT NOT NULL,
    category_id TEXT NOT NULL,
    first_matched_at TEXT NOT NULL,
    last_matched_at TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    PRIMARY KEY (paper_source, paper_source_id, category_id),
    FOREIGN KEY (paper_source, paper_source_id)
        REFERENCES papers(source, source_id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE editions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    subject TEXT NOT NULL,
    text_body TEXT NOT NULL,
    html_body TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('draft', 'sending', 'submitted', 'failed')),
    message_id TEXT,
    submitted_at TEXT,
    error TEXT
);

CREATE TABLE edition_items (
    edition_id TEXT NOT NULL,
    paper_source TEXT NOT NULL,
    paper_source_id TEXT NOT NULL,
    primary_category_id TEXT NOT NULL,
    category_ids_json TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (edition_id, paper_source, paper_source_id),
    FOREIGN KEY (edition_id) REFERENCES editions(id) ON DELETE CASCADE,
    FOREIGN KEY (paper_source, paper_source_id)
        REFERENCES papers(source, source_id),
    FOREIGN KEY (primary_category_id) REFERENCES categories(id)
);

CREATE TABLE deliveries (
    id INTEGER PRIMARY KEY,
    edition_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('sending', 'submitted', 'failed')),
    message_id TEXT,
    error TEXT,
    FOREIGN KEY (edition_id) REFERENCES editions(id)
);

CREATE TABLE paper_summaries (
    id INTEGER PRIMARY KEY,
    paper_source TEXT NOT NULL,
    paper_source_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    takeaway TEXT NOT NULL,
    summary TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    provider_request_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (
        paper_source, paper_source_id, provider, model, prompt_hash, input_hash
    ),
    FOREIGN KEY (paper_source, paper_source_id)
        REFERENCES papers(source, source_id) ON DELETE CASCADE
);

CREATE INDEX idx_runs_status ON runs(status, until_date);
CREATE INDEX idx_paper_categories_category ON paper_categories(category_id);
CREATE INDEX idx_editions_status ON editions(status, created_at);
CREATE INDEX idx_edition_items_paper
    ON edition_items(paper_source, paper_source_id);
CREATE INDEX idx_paper_summaries_paper
    ON paper_summaries(paper_source, paper_source_id);
"""

_MIGRATION_1_TO_2 = """
CREATE TABLE paper_summaries (
    id INTEGER PRIMARY KEY,
    paper_source TEXT NOT NULL,
    paper_source_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    takeaway TEXT NOT NULL,
    summary TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    provider_request_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (
        paper_source, paper_source_id, provider, model, prompt_hash, input_hash
    ),
    FOREIGN KEY (paper_source, paper_source_id)
        REFERENCES papers(source, source_id) ON DELETE CASCADE
);
CREATE INDEX idx_paper_summaries_paper
    ON paper_summaries(paper_source, paper_source_id);
"""


@dataclass(frozen=True, slots=True)
class PendingPaper:
    """An unsent paper with all active category memberships."""

    paper: Paper
    primary_category_id: str
    category_ids: tuple[str, ...]
    summary: PaperSummary | None = None


@dataclass(frozen=True, slots=True)
class StoredEdition:
    """A rendered newsletter edition retained for safe delivery."""

    id: str
    created_at: datetime
    subject: str
    text_body: str
    html_body: str
    status: str
    message_id: str | None
    submitted_at: datetime | None
    error: str | None
    item_count: int


@dataclass(frozen=True, slots=True)
class DatabaseStatus:
    """A compact operational summary of Litletter state."""

    papers: int
    category_matches: int
    submitted_editions: int
    unsent_papers: int
    cached_summaries: int
    last_successful_until: date | None
    open_edition: StoredEdition | None


class Database:
    """Own Litletter's single-user SQLite state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> Database:
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def open(self) -> None:
        """Open an existing database."""
        if self._connection is not None:
            return
        if not self.path.exists():
            raise DatabaseError(
                f"database does not exist: {self.path}; run 'litletter db init'"
            )
        self._connection = sqlite3.connect(self.path, timeout=5)
        self._configure()
        self._require_current_schema()

    def initialize(self) -> None:
        """Create or migrate the database to the current schema."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._connection is None:
            self._connection = sqlite3.connect(self.path, timeout=5)
        self._configure()
        version = self._user_version()
        if version == 0:
            try:
                with self.connection:
                    self.connection.executescript(_SCHEMA)
                    self.connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            except sqlite3.Error as exc:
                raise DatabaseError(f"could not initialize database: {exc}") from exc
        elif version == 1:
            try:
                with self.connection:
                    self.connection.executescript(_MIGRATION_1_TO_2)
                    self.connection.execute("PRAGMA user_version = 2")
            except sqlite3.Error as exc:
                raise DatabaseError(f"could not migrate database: {exc}") from exc
        elif version != _SCHEMA_VERSION:
            raise DatabaseError(
                f"unsupported database schema {version}; expected {_SCHEMA_VERSION}"
            )

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise DatabaseError("database is not open")
        return self._connection

    def sync_categories(self, categories: Sequence[CategoryConfig]) -> None:
        """Persist the active category configuration and its ordering."""
        now = _timestamp()
        with self.connection:
            self.connection.execute("UPDATE categories SET active = 0")
            for position, category in enumerate(categories):
                previous = self.connection.execute(
                    "SELECT query FROM categories WHERE id = ?", (category.id,)
                ).fetchone()
                if previous is not None and previous[0] != category.query:
                    self.connection.execute(
                        "DELETE FROM paper_categories WHERE category_id = ?",
                        (category.id,),
                    )
                self.connection.execute(
                    """
                    INSERT INTO categories (
                        id, name, query, position, active, updated_at
                    )
                    VALUES (?, ?, ?, ?, 1, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        query = excluded.query,
                        position = excluded.position,
                        active = 1,
                        updated_at = excluded.updated_at
                    """,
                    (category.id, category.name, category.query, position, now),
                )

    def start_run(self, since: date, until: date) -> int:
        """Record a running discovery attempt and return its ID."""
        try:
            with self.connection:
                cursor = self.connection.execute(
                    """
                    INSERT INTO runs
                        (started_at, since_date, until_date, status)
                    VALUES (?, ?, ?, 'running')
                    """,
                    (_timestamp(), since.isoformat(), until.isoformat()),
                )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError(f"could not start run: {exc}") from exc
        return int(cursor.lastrowid)

    def fail_interrupted_runs(self) -> int:
        """Close runs left active after a terminated process."""
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE runs
                SET completed_at = ?, status = 'failed',
                    error = 'interrupted before completion'
                WHERE status = 'running'
                """,
                (_timestamp(),),
            )
        return int(cursor.rowcount)

    def finish_run(self, run_id: int, *, error: str | None = None) -> None:
        """Mark a run successful or failed."""
        status = "failed" if error else "succeeded"
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE runs
                SET completed_at = ?, status = ?, error = ?
                WHERE id = ? AND status = 'running'
                """,
                (_timestamp(), status, error, run_id),
            )
        if cursor.rowcount != 1:
            raise DatabaseError(f"run {run_id} is not in progress")

    def last_successful_until(self) -> date | None:
        row = self.connection.execute(
            """
            SELECT until_date FROM runs
            WHERE status = 'succeeded'
            ORDER BY until_date DESC, id DESC LIMIT 1
            """
        ).fetchone()
        return date.fromisoformat(row[0]) if row else None

    def save_matches(
        self,
        category_id: str,
        papers: Iterable[Paper],
        *,
        run_id: int,
    ) -> int:
        """Upsert matched papers and return the number processed."""
        now = _timestamp()
        count = 0
        with self.connection:
            for paper in papers:
                self._upsert_paper(paper, now)
                self.connection.execute(
                    """
                    INSERT INTO paper_categories (
                        paper_source, paper_source_id, category_id,
                        first_matched_at, last_matched_at, run_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(paper_source, paper_source_id, category_id)
                    DO UPDATE SET
                        last_matched_at = excluded.last_matched_at,
                        run_id = excluded.run_id
                    """,
                    (
                        paper.source.value,
                        paper.source_id,
                        category_id,
                        now,
                        now,
                        run_id,
                    ),
                )
                count += 1
        return count

    def unsent_papers(self) -> list[PendingPaper]:
        """Return active matched papers never included in a submitted edition."""
        rows = self.connection.execute(
            """
            SELECT p.*, pc.category_id, c.position
            FROM papers AS p
            JOIN paper_categories AS pc
              ON pc.paper_source = p.source AND pc.paper_source_id = p.source_id
            JOIN categories AS c ON c.id = pc.category_id AND c.active = 1
            WHERE NOT EXISTS (
                SELECT 1
                FROM edition_items AS ei
                JOIN editions AS e ON e.id = ei.edition_id
                WHERE e.status = 'submitted'
                  AND ei.paper_source = p.source
                  AND ei.paper_source_id = p.source_id
            )
            ORDER BY p.published_at DESC, p.source, p.source_id, c.position
            """
        ).fetchall()
        grouped: dict[tuple[str, str], tuple[Paper, list[tuple[int, str]]]] = {}
        for row in rows:
            key = (row[0], row[1])
            if key not in grouped:
                grouped[key] = (_paper_from_row(row), [])
            grouped[key][1].append((int(row[18]), str(row[17])))
        return [
            PendingPaper(
                paper=paper,
                primary_category_id=memberships[0][1],
                category_ids=tuple(category_id for _, category_id in memberships),
            )
            for paper, memberships in grouped.values()
        ]

    def create_edition(
        self,
        *,
        edition_id: str,
        subject: str,
        text_body: str,
        html_body: str,
        items: Sequence[PendingPaper],
    ) -> StoredEdition:
        """Persist an immutable rendered edition and its ordered paper set."""
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO editions
                    (id, created_at, subject, text_body, html_body, status)
                VALUES (?, ?, ?, ?, ?, 'draft')
                """,
                (edition_id, _timestamp(), subject, text_body, html_body),
            )
            self.connection.executemany(
                """
                INSERT INTO edition_items (
                    edition_id, paper_source, paper_source_id,
                    primary_category_id, category_ids_json, position
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        edition_id,
                        item.paper.source.value,
                        item.paper.source_id,
                        item.primary_category_id,
                        json.dumps(item.category_ids),
                        position,
                    )
                    for position, item in enumerate(items)
                ],
            )
        edition = self.get_edition(edition_id)
        if edition is None:
            raise DatabaseError("edition disappeared after creation")
        return edition

    def find_summary(
        self,
        paper: Paper,
        *,
        provider: str,
        model: str,
        prompt_hash: str,
        input_hash: str,
    ) -> PaperSummary | None:
        """Return the exact cached summary identity, if present."""
        row = self.connection.execute(
            """
            SELECT takeaway, summary FROM paper_summaries
            WHERE paper_source = ? AND paper_source_id = ?
              AND provider = ? AND model = ?
              AND prompt_hash = ? AND input_hash = ?
            """,
            (
                paper.source.value,
                paper.source_id,
                provider,
                model,
                prompt_hash,
                input_hash,
            ),
        ).fetchone()
        return PaperSummary(str(row[0]), str(row[1])) if row else None

    def save_summary(self, paper: Paper, result: SummaryResult) -> None:
        """Persist one successfully validated summary and its token usage."""
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO paper_summaries (
                    paper_source, paper_source_id, provider, model,
                    prompt_hash, input_hash, takeaway, summary,
                    input_tokens, output_tokens, provider_request_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    paper_source, paper_source_id, provider, model,
                    prompt_hash, input_hash
                ) DO UPDATE SET
                    takeaway = excluded.takeaway,
                    summary = excluded.summary,
                    input_tokens = excluded.input_tokens,
                    output_tokens = excluded.output_tokens,
                    provider_request_id = excluded.provider_request_id
                """,
                (
                    paper.source.value,
                    paper.source_id,
                    result.provider,
                    result.model,
                    result.prompt_hash,
                    result.input_hash,
                    result.paper_summary.takeaway,
                    result.paper_summary.summary,
                    result.input_tokens,
                    result.output_tokens,
                    result.provider_request_id,
                    _timestamp(),
                ),
            )

    def open_edition(self) -> StoredEdition | None:
        """Return an edition requiring attention before another can be created."""
        row = self.connection.execute(
            """
            SELECT e.*, COUNT(ei.paper_source)
            FROM editions AS e
            LEFT JOIN edition_items AS ei ON ei.edition_id = e.id
            WHERE e.status IN ('draft', 'sending', 'failed')
            GROUP BY e.id
            ORDER BY e.created_at LIMIT 1
            """
        ).fetchone()
        return _edition_from_row(row) if row else None

    def get_edition(self, edition_id: str) -> StoredEdition | None:
        row = self.connection.execute(
            """
            SELECT e.*, COUNT(ei.paper_source)
            FROM editions AS e
            LEFT JOIN edition_items AS ei ON ei.edition_id = e.id
            WHERE e.id = ? GROUP BY e.id
            """,
            (edition_id,),
        ).fetchone()
        return _edition_from_row(row) if row else None

    def begin_delivery(self, edition_id: str, *, provider: str) -> int:
        """Move a draft/failed edition into the uncertain sending state."""
        now = _timestamp()
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE editions SET status = 'sending', error = NULL
                WHERE id = ? AND status IN ('draft', 'failed')
                """,
                (edition_id,),
            )
            if cursor.rowcount != 1:
                raise DatabaseError(
                    f"edition {edition_id!r} is not available for delivery"
                )
            delivery = self.connection.execute(
                """
                INSERT INTO deliveries
                    (edition_id, provider, started_at, status)
                VALUES (?, ?, ?, 'sending')
                """,
                (edition_id, provider, now),
            )
        return int(delivery.lastrowid)

    def complete_delivery(self, delivery_id: int, *, message_id: str) -> None:
        """Atomically mark a provider submission and its edition successful."""
        now = _timestamp()
        with self.connection:
            row = self.connection.execute(
                "SELECT edition_id FROM deliveries WHERE id = ? AND status = 'sending'",
                (delivery_id,),
            ).fetchone()
            if row is None:
                raise DatabaseError(f"delivery {delivery_id} is not in progress")
            self.connection.execute(
                """
                UPDATE deliveries
                SET status = 'submitted', completed_at = ?, message_id = ?
                WHERE id = ?
                """,
                (now, message_id, delivery_id),
            )
            self.connection.execute(
                """
                UPDATE editions
                SET status = 'submitted', submitted_at = ?, message_id = ?, error = NULL
                WHERE id = ?
                """,
                (now, message_id, row[0]),
            )

    def fail_delivery(self, delivery_id: int, *, error: str) -> None:
        """Retain a failed edition for explicit inspection or retry."""
        now = _timestamp()
        with self.connection:
            row = self.connection.execute(
                "SELECT edition_id FROM deliveries WHERE id = ? AND status = 'sending'",
                (delivery_id,),
            ).fetchone()
            if row is None:
                raise DatabaseError(f"delivery {delivery_id} is not in progress")
            self.connection.execute(
                """
                UPDATE deliveries
                SET status = 'failed', completed_at = ?, error = ? WHERE id = ?
                """,
                (now, error, delivery_id),
            )
            self.connection.execute(
                "UPDATE editions SET status = 'failed', error = ? WHERE id = ?",
                (error, row[0]),
            )

    def resolve_uncertain_delivery(
        self,
        edition_id: str,
        *,
        delivered: bool,
        message_id: str | None = None,
    ) -> None:
        """Resolve a `sending` edition after checking the provider manually."""
        if delivered and (message_id is None or not message_id.strip()):
            raise DatabaseError("a provider message ID is required when delivered")
        now = _timestamp()
        with self.connection:
            row = self.connection.execute(
                """
                SELECT id FROM deliveries
                WHERE edition_id = ? AND status = 'sending'
                ORDER BY id DESC LIMIT 1
                """,
                (edition_id,),
            ).fetchone()
            edition = self.connection.execute(
                "SELECT status FROM editions WHERE id = ?", (edition_id,)
            ).fetchone()
            if edition is None:
                raise DatabaseError(f"edition {edition_id!r} does not exist")
            if edition[0] != "sending" or row is None:
                raise DatabaseError(
                    f"edition {edition_id!r} is not awaiting delivery resolution"
                )
            delivery_id = int(row[0])
            if delivered:
                self.connection.execute(
                    """
                    UPDATE deliveries
                    SET status = 'submitted', completed_at = ?, message_id = ?
                    WHERE id = ?
                    """,
                    (now, message_id, delivery_id),
                )
                self.connection.execute(
                    """
                    UPDATE editions
                    SET status = 'submitted', submitted_at = ?, message_id = ?,
                        error = NULL
                    WHERE id = ?
                    """,
                    (now, message_id, edition_id),
                )
            else:
                error = "operator confirmed the provider did not deliver the message"
                self.connection.execute(
                    """
                    UPDATE deliveries
                    SET status = 'failed', completed_at = ?, error = ?
                    WHERE id = ?
                    """,
                    (now, error, delivery_id),
                )
                self.connection.execute(
                    """
                    UPDATE editions SET status = 'failed', error = ? WHERE id = ?
                    """,
                    (error, edition_id),
                )

    def status(self) -> DatabaseStatus:
        """Return counts useful for an operator status command."""
        papers = _count(self.connection, "papers")
        matches = _count(self.connection, "paper_categories")
        submitted = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM editions WHERE status = 'submitted'"
            ).fetchone()[0]
        )
        return DatabaseStatus(
            papers=papers,
            category_matches=matches,
            submitted_editions=submitted,
            unsent_papers=len(self.unsent_papers()),
            cached_summaries=_count(self.connection, "paper_summaries"),
            last_successful_until=self.last_successful_until(),
            open_edition=self.open_edition(),
        )

    def _configure(self) -> None:
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA busy_timeout = 5000")
        except sqlite3.Error as exc:
            raise DatabaseError(f"could not configure database: {exc}") from exc

    def _require_current_schema(self) -> None:
        version = self._user_version()
        if version != _SCHEMA_VERSION:
            raise DatabaseError(
                f"unsupported database schema {version}; expected {_SCHEMA_VERSION}; "
                "run 'litletter db init'"
            )

    def _user_version(self) -> int:
        return int(self.connection.execute("PRAGMA user_version").fetchone()[0])

    def _upsert_paper(self, paper: Paper, now: str) -> None:
        self.connection.execute(
            """
            INSERT INTO papers (
                source, source_id, title, abstract, authors_json,
                published_at, updated_at, doi, url, journal, category, version,
                journal_abbreviation, journal_nlm_id, journal_issns_json,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                title = excluded.title,
                abstract = excluded.abstract,
                authors_json = excluded.authors_json,
                published_at = excluded.published_at,
                updated_at = excluded.updated_at,
                doi = excluded.doi,
                url = excluded.url,
                journal = excluded.journal,
                category = excluded.category,
                version = excluded.version,
                journal_abbreviation = excluded.journal_abbreviation,
                journal_nlm_id = excluded.journal_nlm_id,
                journal_issns_json = excluded.journal_issns_json,
                last_seen_at = excluded.last_seen_at
            """,
            (
                paper.source.value,
                paper.source_id,
                paper.title,
                paper.abstract,
                json.dumps(
                    [
                        {"name": author.name, "orcid": author.orcid}
                        for author in paper.authors
                    ]
                ),
                _date_text(paper.published_at),
                _date_text(paper.updated_at),
                paper.doi,
                paper.url,
                paper.journal,
                paper.category,
                paper.version,
                paper.journal_abbreviation,
                paper.journal_nlm_id,
                json.dumps(paper.journal_issns),
                now,
                now,
            ),
        )


def _paper_from_row(row: sqlite3.Row | tuple[object, ...]) -> Paper:
    authors = json.loads(str(row[4]))
    return Paper(
        source=PaperSource(str(row[0])),
        source_id=str(row[1]),
        title=str(row[2]),
        abstract=str(row[3]) if row[3] is not None else None,
        authors=tuple(Author(item["name"], item.get("orcid")) for item in authors),
        published_at=_parse_date(row[5]),
        updated_at=_parse_date(row[6]),
        doi=str(row[7]) if row[7] is not None else None,
        url=str(row[8]),
        journal=str(row[9]) if row[9] is not None else None,
        category=str(row[10]) if row[10] is not None else None,
        version=int(row[11]) if row[11] is not None else None,
        journal_abbreviation=str(row[12]) if row[12] is not None else None,
        journal_nlm_id=str(row[13]) if row[13] is not None else None,
        journal_issns=tuple(json.loads(str(row[14]))),
    )


def _edition_from_row(row: sqlite3.Row | tuple[object, ...]) -> StoredEdition:
    return StoredEdition(
        id=str(row[0]),
        created_at=datetime.fromisoformat(str(row[1])),
        subject=str(row[2]),
        text_body=str(row[3]),
        html_body=str(row[4]),
        status=str(row[5]),
        message_id=str(row[6]) if row[6] is not None else None,
        submitted_at=datetime.fromisoformat(str(row[7])) if row[7] else None,
        error=str(row[8]) if row[8] is not None else None,
        item_count=int(row[9]),
    )


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _parse_date(value: object) -> date | None:
    return date.fromisoformat(str(value)) if value is not None else None
