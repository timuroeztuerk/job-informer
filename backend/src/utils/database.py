"""SQLite persistence for the active collect, filter, and review workflow.

Older databases may still contain tables and columns from retired experiments.
Initialization deliberately leaves those objects untouched, while new databases
only receive tables used by the current product.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

import pandas as pd
from loguru import logger

from .data_utils import build_normalized_key, normalize_job_url
from .job_consolidation import initialize_consolidation, rebuild_consolidation, member_job_ids
from .sqlite_connection import connect_sqlite, open_sqlite
from .time_utils import normalize_utc_iso, utc_now_iso
from ..descriptions.schema import initialize_schema as initialize_description_schema
from ..descriptions.legacy import import_legacy_descriptions
from ..ai.store import initialize_schema as initialize_ai_schema


DEFAULT_DB_PATH = str(Path(__file__).resolve().parents[2] / "data" / "jobs.db")


def resolve_database_path(db_path: str | Path | None = None) -> str:
    """Resolve an explicit path, or the current ``JOBS_DB_PATH`` value."""
    if db_path is None:
        raw_path = os.getenv("JOBS_DB_PATH", "").strip() or DEFAULT_DB_PATH
    else:
        raw_path = str(db_path).strip()
        if not raw_path:
            raise ValueError("Database path cannot be empty")

    if raw_path == ":memory:":
        return raw_path
    return str(Path(raw_path).expanduser().resolve())


class JobDatabase:
    """Small SQLite store for jobs, sightings, filters, and scrape runs."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = resolve_database_path(db_path)
        self._ensure_data_dir()
        self._init_db()

    def _ensure_data_dir(self) -> None:
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a configured connection and always close it."""
        with open_sqlite(self.db_path) as conn:
            yield conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _get_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}

    def _init_db(self) -> None:
        """Create the current schema and apply additive legacy migrations."""
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL,
                    source TEXT NOT NULL,
                    url TEXT,
                    salary TEXT,
                    normalized_key TEXT,
                    scraped_at TIMESTAMP NOT NULL,
                    first_seen_at TIMESTAMP NOT NULL,
                    last_seen_at TIMESTAMP NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    archived_at TIMESTAMP,
                    archived_reason TEXT,
                    is_favorite INTEGER NOT NULL DEFAULT 0
                        CHECK(is_favorite IN (0, 1)),
                    is_flagged INTEGER NOT NULL DEFAULT 0
                        CHECK(is_flagged IN (0, 1)),
                    flag_reason TEXT,
                    flagged_at TIMESTAMP,
                    relevance_outcome TEXT,
                    role_family TEXT,
                    relevance_reason TEXT,
                    relevance_ruleset_version TEXT,
                    relevance_evaluated_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
                );

                CREATE TABLE IF NOT EXISTS scrape_runs (
                    scrape_run_id TEXT PRIMARY KEY,
                    api_run_id TEXT,
                    mode TEXT NOT NULL DEFAULT 'run-once',
                    keywords TEXT,
                    locations TEXT,
                    observed_at TIMESTAMP NOT NULL,
                    observed_jobs_count INTEGER NOT NULL DEFAULT 0,
                    new_jobs_count INTEGER NOT NULL DEFAULT 0,
                    archived_jobs_count INTEGER NOT NULL DEFAULT 0,
                    coverage_json TEXT NOT NULL DEFAULT '[]',
                    relevance_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
                    completed_at TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS job_observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    scrape_run_id TEXT,
                    observed_at TIMESTAMP NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL,
                    source TEXT NOT NULL,
                    url TEXT,
                    salary TEXT,
                    is_new INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                        ON UPDATE CASCADE ON DELETE RESTRICT,
                    FOREIGN KEY(scrape_run_id) REFERENCES scrape_runs(scrape_run_id)
                        ON UPDATE CASCADE ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS filter_decisions (
                    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    decision_source TEXT NOT NULL,
                    decision_action TEXT NOT NULL DEFAULT 'archive',
                    filter_name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    matched_value TEXT,
                    confidence REAL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    decided_at TIMESTAMP NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                        ON UPDATE CASCADE ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS relevance_validation_labels (
                    job_id TEXT NOT NULL,
                    label TEXT NOT NULL
                        CHECK(label IN ('target', 'adjacent', 'noise')),
                    notes TEXT NOT NULL DEFAULT '',
                    policy_version TEXT NOT NULL,
                    labeled_at TIMESTAMP NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
                    PRIMARY KEY(job_id, policy_version),
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                        ON UPDATE CASCADE ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS query_groups (
                    query_group_key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
                    updated_at TIMESTAMP NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
                );

                CREATE TABLE IF NOT EXISTS query_terms (
                    query_term_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_group_key TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
                    updated_at TIMESTAMP NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
                    UNIQUE(query_group_key, query_text),
                    FOREIGN KEY(query_group_key) REFERENCES query_groups(query_group_key)
                        ON UPDATE CASCADE ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS collection_queries (
                    collection_query_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scrape_run_id TEXT NOT NULL,
                    query_term_id INTEGER,
                    query_group_key TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    location TEXT NOT NULL,
                    page_offsets_json TEXT NOT NULL DEFAULT '[]',
                    pages_attempted INTEGER NOT NULL DEFAULT 0,
                    pages_completed INTEGER NOT NULL DEFAULT 0,
                    raw_cards INTEGER NOT NULL DEFAULT 0,
                    valid_jobs INTEGER NOT NULL DEFAULT 0,
                    duplicate_cards INTEGER NOT NULL DEFAULT 0,
                    request_attempts INTEGER NOT NULL DEFAULT 0,
                    request_failures INTEGER NOT NULL DEFAULT 0,
                    rate_limit_responses INTEGER NOT NULL DEFAULT 0,
                    stop_reason TEXT,
                    last_status INTEGER,
                    last_error TEXT,
                    created_at TIMESTAMP NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
                    UNIQUE(scrape_run_id, query_text, location),
                    FOREIGN KEY(scrape_run_id) REFERENCES scrape_runs(scrape_run_id)
                        ON UPDATE CASCADE ON DELETE RESTRICT,
                    FOREIGN KEY(query_term_id) REFERENCES query_terms(query_term_id)
                        ON UPDATE CASCADE ON DELETE SET NULL,
                    FOREIGN KEY(query_group_key) REFERENCES query_groups(query_group_key)
                        ON UPDATE CASCADE ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS job_query_matches (
                    job_id TEXT NOT NULL,
                    collection_query_id INTEGER NOT NULL,
                    first_page_offset INTEGER,
                    created_at TIMESTAMP NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
                    PRIMARY KEY(job_id, collection_query_id),
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                        ON UPDATE CASCADE ON DELETE RESTRICT,
                    FOREIGN KEY(collection_query_id) REFERENCES collection_queries(collection_query_id)
                        ON UPDATE CASCADE ON DELETE RESTRICT
                );

                CREATE VIEW IF NOT EXISTS filtered_jobs AS
                SELECT * FROM jobs
                WHERE archived_at IS NULL AND LOWER(source) = 'linkedin';
                """
            )

            self._ensure_jobs_columns(conn)
            initialize_description_schema(conn)
            initialize_ai_schema(conn)
            import_legacy_descriptions(conn)
            self._ensure_scrape_run_columns(conn)
            self._create_indexes_and_guards(conn)
            self._backfill_observation_fields(conn)
            initialize_consolidation(conn)
            conn.commit()

    def _ensure_jobs_columns(self, conn: sqlite3.Connection) -> None:
        """Add only current-product columns when opening an older database."""
        columns = self._get_table_columns(conn, "jobs")
        additions = {
            "normalized_key": "TEXT",
            "first_seen_at": "TIMESTAMP",
            "last_seen_at": "TIMESTAMP",
            "seen_count": "INTEGER DEFAULT 1",
            "archived_at": "TIMESTAMP",
            "archived_reason": "TEXT",
            "is_favorite": "INTEGER NOT NULL DEFAULT 0 CHECK(is_favorite IN (0, 1))",
            "is_flagged": "INTEGER NOT NULL DEFAULT 0 CHECK(is_flagged IN (0, 1))",
            "flag_reason": "TEXT",
            "flagged_at": "TIMESTAMP",
            "relevance_outcome": "TEXT",
            "role_family": "TEXT",
            "relevance_reason": "TEXT",
            "relevance_ruleset_version": "TEXT",
            "relevance_evaluated_at": "TIMESTAMP",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")

    def _ensure_scrape_run_columns(self, conn: sqlite3.Connection) -> None:
        """Add active run fields to databases created by older versions."""
        columns = self._get_table_columns(conn, "scrape_runs")
        additions = {
            "api_run_id": "TEXT",
            "archived_jobs_count": "INTEGER NOT NULL DEFAULT 0",
            "coverage_json": "TEXT NOT NULL DEFAULT '[]'",
            "relevance_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE scrape_runs ADD COLUMN {name} {definition}")

    def _create_indexes_and_guards(self, conn: sqlite3.Connection) -> None:
        indexes = (
            "CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_scraped_at ON jobs(scraped_at)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_archived_at ON jobs(archived_at)",
            "CREATE INDEX IF NOT EXISTS idx_observations_job ON job_observations(job_id, observed_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_observations_run ON job_observations(scrape_run_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_job_seen ON job_observations(job_id, observed_at)",
            "CREATE INDEX IF NOT EXISTS idx_scrape_runs_api_run ON scrape_runs(api_run_id)",
            "CREATE INDEX IF NOT EXISTS idx_filter_decisions_job ON filter_decisions(job_id, decided_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_validation_labels_label ON relevance_validation_labels(label, policy_version)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_role_family ON jobs(role_family)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_relevance_outcome ON jobs(relevance_outcome)",
            "CREATE INDEX IF NOT EXISTS idx_query_terms_group ON query_terms(query_group_key, enabled, sort_order)",
            "CREATE INDEX IF NOT EXISTS idx_collection_queries_run ON collection_queries(scrape_run_id)",
            "CREATE INDEX IF NOT EXISTS idx_collection_queries_group ON collection_queries(query_group_key)",
            "CREATE INDEX IF NOT EXISTS idx_job_query_matches_job ON job_query_matches(job_id)",
        )
        for statement in indexes:
            conn.execute(statement)

        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_normalized_key "
                "ON jobs(normalized_key) WHERE normalized_key IS NOT NULL AND normalized_key != ''"
            )
        except sqlite3.IntegrityError:
            logger.warning("Legacy duplicate identities prevent a unique normalized-key index")

        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_job_run "
                "ON job_observations(job_id, scrape_run_id) WHERE scrape_run_id IS NOT NULL"
            )
        except sqlite3.IntegrityError:
            logger.warning("Legacy duplicate run observations prevent a unique job/run index")

        # Triggers protect older installations whose tables predate foreign keys.
        for table in (
            "job_observations",
            "filter_decisions",
            "relevance_validation_labels",
            "job_query_matches",
        ):
            conn.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{table}_job_exists
                BEFORE INSERT ON {table}
                FOR EACH ROW
                WHEN NOT EXISTS (SELECT 1 FROM jobs WHERE job_id = NEW.job_id)
                BEGIN
                    SELECT RAISE(ABORT, '{table}.job_id has no matching job');
                END
                """
            )

    def _backfill_observation_fields(self, conn: sqlite3.Connection) -> None:
        """Populate sighting fields and one baseline observation for legacy jobs."""
        conn.execute(
            """
            UPDATE jobs
            SET first_seen_at = COALESCE(first_seen_at, scraped_at, created_at),
                last_seen_at = COALESCE(last_seen_at, scraped_at, created_at),
                seen_count = CASE WHEN seen_count IS NULL OR seen_count < 1 THEN 1 ELSE seen_count END
            WHERE first_seen_at IS NULL OR last_seen_at IS NULL
               OR seen_count IS NULL OR seen_count < 1
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO job_observations (
                job_id, scrape_run_id, observed_at, title, company,
                location, source, url, salary, is_new
            )
            SELECT job_id, NULL, COALESCE(last_seen_at, scraped_at, created_at),
                   title, company, location, source, url, salary, 1
            FROM jobs
            WHERE NOT EXISTS (
                SELECT 1 FROM job_observations o WHERE o.job_id = jobs.job_id
            )
            """
        )
        conn.execute(
            """
            UPDATE jobs
            SET first_seen_at = COALESCE(
                    (SELECT MIN(observed_at) FROM job_observations o WHERE o.job_id = jobs.job_id),
                    first_seen_at, scraped_at, created_at
                ),
                last_seen_at = COALESCE(
                    (SELECT MAX(observed_at) FROM job_observations o WHERE o.job_id = jobs.job_id),
                    last_seen_at, scraped_at, created_at
                ),
                seen_count = COALESCE(
                    (SELECT COUNT(*) FROM job_observations o WHERE o.job_id = jobs.job_id),
                    seen_count, 1
                )
            """
        )

    @staticmethod
    def _normalize_timestamp(value: Any) -> str | None:
        return normalize_utc_iso(value)

    @classmethod
    def _pick_earliest_timestamp(cls, *values: Any) -> str | None:
        candidates = [value for item in values if (value := cls._normalize_timestamp(item))]
        return min(candidates) if candidates else None

    @classmethod
    def _pick_latest_timestamp(cls, *values: Any) -> str | None:
        candidates = [value for item in values if (value := cls._normalize_timestamp(item))]
        return max(candidates) if candidates else None

    @staticmethod
    def _serialize_run_payload(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, (list, tuple, set)):
            values = [str(item).strip() for item in value if str(item).strip()]
            return json.dumps(values) if values else None
        return str(value)

    @staticmethod
    def _clean_value(value: Any, default: str = "") -> str:
        if value is None:
            return default
        try:
            if bool(pd.isna(value)):
                return default
        except (TypeError, ValueError):
            pass
        clean = str(value).strip()
        return clean or default

    @staticmethod
    def _job_identity_aliases(
        *,
        job_id: Any,
        normalized_key: Any,
        url: Any,
        source: Any,
        title: Any,
        company: Any,
    ) -> list[str]:
        """Return canonical aliases for both current and legacy identity values."""
        aliases: list[str] = []
        for value in (
            normalized_key,
            job_id,
            url,
            build_normalized_key(url=url, source=source, title=title, company=company),
        ):
            normalized = normalize_job_url(value, source)
            if normalized and normalized not in aliases:
                aliases.append(normalized)
        return aliases

    @staticmethod
    def _resolve_stored_job_id(conn: sqlite3.Connection, incoming_id: str) -> str:
        """Resolve a current canonical ID to an older stored row when needed."""
        normalized = normalize_job_url(incoming_id, "LinkedIn") or incoming_id
        row = conn.execute(
            """
            SELECT job_id FROM jobs
            WHERE job_id = ? OR normalized_key = ? OR normalized_key = ?
            ORDER BY CASE WHEN job_id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (incoming_id, incoming_id, normalized, incoming_id),
        ).fetchone()
        return str(row[0]) if row is not None else incoming_id

    def record_filter_decisions(
        self,
        decisions: list[dict[str, Any]],
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """Persist deterministic filter decisions for auditability."""
        payloads: list[tuple[Any, ...]] = []
        for decision in decisions:
            job_id = str(decision.get("job_id") or "").strip()
            filter_name = str(decision.get("filter_name") or "").strip()
            reason = str(decision.get("reason") or "").strip()
            if not job_id or not filter_name or not reason:
                continue

            try:
                confidence = (
                    None
                    if decision.get("confidence") in (None, "")
                    else float(decision["confidence"])
                )
            except (TypeError, ValueError):
                confidence = None
            details = decision.get("details")
            details_json = details.strip() if isinstance(details, str) else json.dumps(details or {})
            payloads.append(
                (
                    job_id,
                    str(decision.get("decision_source") or "rule").strip() or "rule",
                    str(decision.get("decision_action") or "archive").strip() or "archive",
                    filter_name,
                    reason,
                    str(decision.get("matched_value") or "").strip() or None,
                    confidence,
                    details_json or "{}",
                    self._normalize_timestamp(decision.get("decided_at")) or utc_now_iso(),
                )
            )
        if not payloads:
            return 0

        owns_connection = conn is None
        active_conn = conn or connect_sqlite(self.db_path)
        try:
            payloads = [
                (
                    self._resolve_stored_job_id(active_conn, str(payload[0])),
                    *payload[1:],
                )
                for payload in payloads
            ]
            active_conn.executemany(
                """
                INSERT INTO filter_decisions (
                    job_id, decision_source, decision_action, filter_name, reason,
                    matched_value, confidence, details_json, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payloads,
            )
            if owns_connection:
                active_conn.commit()
            return len(payloads)
        finally:
            if owns_connection:
                active_conn.close()

    def archive_jobs(
        self,
        job_ids: list[str],
        *,
        archived_reason: str,
        archived_at: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """Soft-archive active jobs and retain their history."""
        normalized_ids = list(dict.fromkeys(str(item).strip() for item in job_ids if str(item).strip()))
        if not normalized_ids:
            return 0

        owns_connection = conn is None
        active_conn = conn or connect_sqlite(self.db_path)
        try:
            normalized_ids = list(
                dict.fromkeys(
                    self._resolve_stored_job_id(active_conn, job_id)
                    for job_id in normalized_ids
                )
            )
            when = self._normalize_timestamp(archived_at) or utc_now_iso()
            reason = archived_reason.strip() or "Archived"
            count = 0
            for start in range(0, len(normalized_ids), 500):
                chunk = normalized_ids[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                active = [
                    str(row[0])
                    for row in active_conn.execute(
                        f"SELECT job_id FROM jobs WHERE archived_at IS NULL AND job_id IN ({placeholders})",
                        chunk,
                    )
                ]
                active_conn.executemany(
                    "UPDATE jobs SET archived_at = ?, archived_reason = ? WHERE job_id = ?",
                    [(when, reason, job_id) for job_id in active],
                )
                count += len(active)
            if owns_connection:
                active_conn.commit()
            return count
        finally:
            if owns_connection:
                active_conn.close()

    def archive_jobs_with_filter_decisions(
        self,
        decisions: list[dict[str, Any]],
        *,
        default_reason: str = "Archived by filter",
    ) -> dict[str, int]:
        """Archive jobs and persist the reasons in one transaction."""
        if not decisions:
            return {"archived": 0, "decisions_recorded": 0}

        jobs_by_reason: dict[str, list[str]] = {}
        for decision in decisions:
            job_id = str(decision.get("job_id") or "").strip()
            reason = str(decision.get("reason") or "").strip() or default_reason
            action = str(decision.get("decision_action") or "archive").strip().lower()
            if job_id and action == "archive":
                jobs_by_reason.setdefault(reason, []).append(job_id)

        with self._get_connection() as conn:
            recorded = self.record_filter_decisions(decisions, conn=conn)
            archived = sum(
                self.archive_jobs(job_ids, archived_reason=reason, conn=conn)
                for reason, job_ids in jobs_by_reason.items()
            )
            for decision in decisions:
                if (
                    str(decision.get("decision_source") or "").lower() == "manual"
                    and str(decision.get("decision_action") or "").lower() == "archive"
                ):
                    stored_id = self._resolve_stored_job_id(
                        conn,
                        self._clean_value(decision.get("job_id")),
                    )
                    conn.execute(
                        """
                        UPDATE jobs
                        SET relevance_outcome = 'manual_archive', role_family = NULL,
                            relevance_reason = ?, relevance_evaluated_at = ?
                        WHERE job_id = ?
                        """,
                        (
                            self._clean_value(decision.get("reason"), "Archived manually"),
                            utc_now_iso(),
                            stored_id,
                        ),
                    )
            conn.commit()
        return {"archived": archived, "decisions_recorded": recorded}

    def restore_job(
        self,
        job_id: str,
        *,
        reason: str = "Job restored for reconsideration",
        decision_source: str = "manual",
    ) -> bool:
        """Restore an archived job and record the manual decision."""
        normalized_id = str(job_id or "").strip()
        if not normalized_id:
            return False
        with self._get_connection() as conn:
            row = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (normalized_id,)).fetchone()
            if row is None:
                return False
            members = member_job_ids(conn, normalized_id)
            conn.execute(
                """
                UPDATE jobs
                SET archived_at = NULL, archived_reason = NULL,
                    relevance_outcome = 'manual_keep', role_family = NULL,
                    relevance_reason = ?, relevance_evaluated_at = ?
                WHERE job_id IN (SELECT job_id FROM job_memberships WHERE canonical_job_id =
                    (SELECT canonical_job_id FROM job_memberships WHERE job_id = ?))
                """,
                (reason, utc_now_iso(), normalized_id),
            )
            self.record_filter_decisions(
                [{
                    "job_id": member,
                    "decision_source": decision_source,
                    "decision_action": "restore",
                    "filter_name": "manual_restore",
                    "reason": reason,
                } for member in members],
                conn=conn,
            )
            conn.commit()
        return True

    def set_job_favorite(self, job_id: str, is_favorite: bool) -> bool:
        """Set the independent favorite flag for one stored job."""
        normalized_id = str(job_id or "").strip()
        if not normalized_id:
            return False
        with self._get_connection() as conn:
            cursor = conn.execute(
                """UPDATE jobs SET is_favorite = ? WHERE job_id IN (
                    SELECT job_id FROM job_memberships WHERE canonical_job_id =
                    (SELECT canonical_job_id FROM job_memberships WHERE job_id = ?))""",
                (1 if is_favorite else 0, normalized_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def set_job_flag(
        self, job_id: str, is_flagged: bool, reason: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """Keep personal mismatch examples independent of automated decisions."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs SET
                    is_flagged = ?,
                    flag_reason = CASE WHEN ? THEN COALESCE(?, flag_reason) ELSE NULL END,
                    flagged_at = CASE WHEN ? THEN COALESCE(flagged_at, ?) ELSE NULL END
                WHERE job_id IN (SELECT job_id FROM job_memberships WHERE canonical_job_id =
                    (SELECT canonical_job_id FROM job_memberships WHERE job_id = ?))
                    AND LOWER(source) = 'linkedin'
                """,
                (int(is_flagged), is_flagged, reason.strip() if reason is not None else None,
                 is_flagged, utc_now_iso(), job_id),
            )
            if not cursor.rowcount:
                return None
            row = conn.execute(
                """SELECT job_id, is_flagged, flag_reason, flagged_at FROM review_jobs
                    WHERE job_id = (SELECT canonical_job_id FROM job_memberships WHERE job_id = ?)""",
                (job_id,),
            ).fetchone()
            conn.commit()
        return {"job_id": row[0], "is_flagged": bool(row[1]), "flag_reason": row[2], "flagged_at": row[3]}

    def start_scrape_run(
        self,
        *,
        mode: str = "run-once",
        api_run_id: Optional[str] = None,
        keywords: Any = None,
        locations: Any = None,
        observed_at: Optional[str] = None,
    ) -> str:
        """Create the record for one manually launched LinkedIn collection."""
        scrape_run_id = f"scrape_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO scrape_runs (
                    scrape_run_id, api_run_id, mode, keywords, locations,
                    observed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scrape_run_id,
                    (api_run_id or os.getenv("JOB_INFORMER_API_RUN_ID", "")).strip() or None,
                    (mode or "run-once").strip() or "run-once",
                    self._serialize_run_payload(keywords),
                    self._serialize_run_payload(locations),
                    self._normalize_timestamp(observed_at) or utc_now_iso(),
                    utc_now_iso(),
                ),
            )
            conn.commit()
        return scrape_run_id

    def finish_scrape_run(
        self,
        scrape_run_id: str,
        *,
        observed_jobs_count: Optional[int] = None,
        new_jobs_count: Optional[int] = None,
        archived_jobs_count: Optional[int] = None,
        coverage: Optional[list[dict[str, Any]]] = None,
        relevance: Optional[dict[str, Any]] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        """Complete a collection record and update the supplied counters."""
        if not scrape_run_id:
            return
        updates = ["completed_at = ?"]
        params: list[Any] = [self._normalize_timestamp(completed_at) or utc_now_iso()]
        for column, value in (
            ("observed_jobs_count", observed_jobs_count),
            ("new_jobs_count", new_jobs_count),
            ("archived_jobs_count", archived_jobs_count),
        ):
            if value is not None:
                updates.append(f"{column} = ?")
                params.append(max(0, int(value)))
        if coverage is not None:
            updates.append("coverage_json = ?")
            params.append(json.dumps(coverage))
        if relevance is not None:
            updates.append("relevance_json = ?")
            params.append(json.dumps(relevance))
        params.append(scrape_run_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE scrape_runs SET {', '.join(updates)} WHERE scrape_run_id = ?",
                params,
            )
            conn.commit()

    def record_collection_queries(
        self,
        scrape_run_id: str,
        reports: list[dict[str, Any]],
    ) -> dict[tuple[str, str], int]:
        """Normalize per-query coverage while retaining the legacy JSON snapshot."""
        if not scrape_run_id or not reports:
            return {}

        query_ids: dict[tuple[str, str], int] = {}
        now = utc_now_iso()
        with self._get_connection() as conn:
            for report in reports:
                query_text = self._clean_value(report.get("keyword"))
                location = self._clean_value(report.get("location"))
                group_key = self._clean_value(report.get("query_group_key"), "custom")
                display_name = self._clean_value(
                    report.get("query_group_name"),
                    group_key.replace("_", " ").title(),
                )
                if not query_text or not location:
                    continue

                conn.execute(
                    """
                    INSERT INTO query_groups (
                        query_group_key, display_name, enabled, updated_at
                    ) VALUES (?, ?, 1, ?)
                    ON CONFLICT(query_group_key) DO UPDATE SET
                        display_name = excluded.display_name,
                        updated_at = excluded.updated_at
                    """,
                    (group_key, display_name, now),
                )
                conn.execute(
                    """
                    INSERT INTO query_terms (
                        query_group_key, query_text, enabled, updated_at
                    ) VALUES (?, ?, 1, ?)
                    ON CONFLICT(query_group_key, query_text) DO UPDATE SET
                        enabled = 1,
                        updated_at = excluded.updated_at
                    """,
                    (group_key, query_text, now),
                )
                query_term_id = int(
                    conn.execute(
                        "SELECT query_term_id FROM query_terms WHERE query_group_key = ? AND query_text = ?",
                        (group_key, query_text),
                    ).fetchone()[0]
                )
                values = (
                    query_term_id,
                    group_key,
                    json.dumps(report.get("page_offsets") or []),
                    max(0, int(report.get("pages_attempted", 0) or 0)),
                    max(0, int(report.get("pages_completed", 0) or 0)),
                    max(0, int(report.get("raw_cards", 0) or 0)),
                    max(0, int(report.get("valid_jobs", 0) or 0)),
                    max(0, int(report.get("duplicate_cards", 0) or 0)),
                    max(0, int(report.get("request_attempts", 0) or 0)),
                    max(0, int(report.get("request_failures", 0) or 0)),
                    max(0, int(report.get("rate_limit_responses", 0) or 0)),
                    self._clean_value(report.get("stop_reason")) or None,
                    report.get("last_status"),
                    self._clean_value(report.get("last_error")) or None,
                )
                conn.execute(
                    """
                    INSERT INTO collection_queries (
                        scrape_run_id, query_term_id, query_group_key, query_text,
                        location, page_offsets_json, pages_attempted, pages_completed,
                        raw_cards, valid_jobs, duplicate_cards, request_attempts,
                        request_failures, rate_limit_responses, stop_reason,
                        last_status, last_error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scrape_run_id, query_text, location) DO UPDATE SET
                        query_term_id = excluded.query_term_id,
                        query_group_key = excluded.query_group_key,
                        page_offsets_json = excluded.page_offsets_json,
                        pages_attempted = excluded.pages_attempted,
                        pages_completed = excluded.pages_completed,
                        raw_cards = excluded.raw_cards,
                        valid_jobs = excluded.valid_jobs,
                        duplicate_cards = excluded.duplicate_cards,
                        request_attempts = excluded.request_attempts,
                        request_failures = excluded.request_failures,
                        rate_limit_responses = excluded.rate_limit_responses,
                        stop_reason = excluded.stop_reason,
                        last_status = excluded.last_status,
                        last_error = excluded.last_error
                    """,
                    (scrape_run_id, query_term_id, group_key, query_text, location, *values[2:]),
                )
                collection_query_id = int(
                    conn.execute(
                        """
                        SELECT collection_query_id FROM collection_queries
                        WHERE scrape_run_id = ? AND query_text = ? AND location = ?
                        """,
                        (scrape_run_id, query_text, location),
                    ).fetchone()[0]
                )
                query_ids[(query_text, location)] = collection_query_id
            conn.commit()
        return query_ids

    def record_job_query_matches(
        self,
        scrape_run_id: str,
        matches: list[dict[str, Any]],
    ) -> int:
        """Store every retrieval path without creating extra repeat sightings."""
        if not scrape_run_id or not matches:
            return 0

        inserted = 0
        with self._get_connection() as conn:
            query_ids = {
                (str(row[1]), str(row[2])): int(row[0])
                for row in conn.execute(
                    """
                    SELECT collection_query_id, query_text, location
                    FROM collection_queries WHERE scrape_run_id = ?
                    """,
                    (scrape_run_id,),
                )
            }
            for match in matches:
                incoming_id = self._clean_value(match.get("job_id"))
                canonical_key = normalize_job_url(incoming_id, "LinkedIn") or incoming_id
                job_row = conn.execute(
                    """
                    SELECT job_id FROM jobs
                    WHERE job_id = ? OR normalized_key = ?
                    ORDER BY CASE WHEN job_id = ? THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (incoming_id, canonical_key, incoming_id),
                ).fetchone()
                query_id = query_ids.get(
                    (
                        self._clean_value(match.get("query_text")),
                        self._clean_value(match.get("query_location")),
                    )
                )
                if job_row is None or query_id is None:
                    continue
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT OR IGNORE INTO job_query_matches (
                        job_id, collection_query_id, first_page_offset
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        str(job_row[0]),
                        query_id,
                        int(match.get("query_page_offset", 0) or 0),
                    ),
                )
                inserted += conn.total_changes - before
            conn.commit()
        return inserted

    def get_manual_overrides(self, job_ids: Optional[list[str]] = None) -> dict[str, str]:
        """Return current explicit keep/archive intent for jobs."""
        params: list[Any] = []
        job_clause = ""
        if job_ids:
            normalized_ids = list(dict.fromkeys(str(item) for item in job_ids if str(item)))
            placeholders = ",".join("?" for _ in normalized_ids)
            job_clause = f" WHERE job_id IN ({placeholders})"
            params.extend(normalized_ids)

        latest: dict[str, tuple[str, str]] = {}
        with self._get_connection() as conn:
            # Keep honoring old explicit choices without recreating the retired
            # personal-notes feature in new databases.
            if self._table_exists(conn, "job_annotations"):
                for row in conn.execute(
                    f"SELECT job_id, status, updated_at FROM job_annotations{job_clause}",
                    params,
                ):
                    status = str(row[1] or "")
                    action = "keep" if status == "interesting" else "archive" if status == "rejected" else ""
                    if action:
                        latest[str(row[0])] = (str(row[2] or ""), action)

            decision_clause = "decision_source = 'manual'"
            decision_params: list[Any] = []
            if job_ids:
                placeholders = ",".join("?" for _ in normalized_ids)
                decision_clause += f" AND job_id IN ({placeholders})"
                decision_params.extend(normalized_ids)
            for row in conn.execute(
                f"""
                SELECT job_id, decision_action, decided_at
                FROM filter_decisions
                WHERE {decision_clause}
                ORDER BY decided_at, decision_id
                """,
                decision_params,
            ):
                action_value = str(row[1] or "").lower()
                action = "keep" if action_value in {"keep", "restore"} else "archive" if action_value == "archive" else ""
                job_id = str(row[0])
                decided_at = str(row[2] or "")
                if action and (job_id not in latest or decided_at >= latest[job_id][0]):
                    latest[job_id] = (decided_at, action)
        return {job_id: value[1] for job_id, value in latest.items()}

    def save_relevance_validation_labels(
        self,
        labels: list[dict[str, Any]],
    ) -> int:
        """Store operator policy labels without changing review or archive state."""
        if not labels:
            return 0

        saved = 0
        valid_labels = {"target", "adjacent", "noise"}
        with self._get_connection() as conn:
            for item in labels:
                job_id = self._clean_value(item.get("job_id"))
                label = self._clean_value(item.get("label")).lower()
                policy_version = self._clean_value(item.get("policy_version"))
                if not job_id or label not in valid_labels or not policy_version:
                    raise ValueError("Validation labels require job_id, target/adjacent/noise, and policy_version")
                stored_job_id = self._resolve_stored_job_id(conn, job_id)
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT INTO relevance_validation_labels (
                        job_id, label, notes, policy_version, labeled_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(job_id, policy_version) DO UPDATE SET
                        label = excluded.label,
                        notes = excluded.notes,
                        labeled_at = excluded.labeled_at
                    """,
                    (
                        stored_job_id,
                        label,
                        self._clean_value(item.get("notes")),
                        policy_version,
                        self._normalize_timestamp(item.get("labeled_at")) or utc_now_iso(),
                    ),
                )
                saved += conn.total_changes - before
            conn.commit()
        return saved

    def save_relevance_evaluations(
        self,
        evaluations: list[dict[str, Any]],
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """Persist current relevance state without changing archive state."""
        owns_connection = conn is None
        active_conn = conn or connect_sqlite(self.db_path)
        saved = 0
        try:
            for evaluation in evaluations:
                job_id = self._clean_value(evaluation.get("job_id"))
                outcome = self._clean_value(evaluation.get("outcome"))
                if not job_id or not outcome:
                    continue
                job_id = self._resolve_stored_job_id(active_conn, job_id)
                active_conn.execute(
                    """
                    UPDATE jobs
                    SET relevance_outcome = ?, role_family = ?, relevance_reason = ?,
                        relevance_ruleset_version = ?, relevance_evaluated_at = ?
                    WHERE job_id = ?
                    """,
                    (
                        outcome,
                        self._clean_value(evaluation.get("role_family")) or None,
                        self._clean_value(evaluation.get("reason")) or None,
                        self._clean_value(evaluation.get("ruleset_version")) or None,
                        self._normalize_timestamp(evaluation.get("evaluated_at")) or utc_now_iso(),
                        job_id,
                    ),
                )
                saved += active_conn.execute("SELECT changes()").fetchone()[0]
            if owns_connection:
                active_conn.commit()
            return saved
        finally:
            if owns_connection:
                active_conn.close()

    def apply_relevance_evaluations(
        self,
        evaluations: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Persist a classification batch and its archive decisions atomically."""
        with self._get_connection() as conn:
            evaluated = self.save_relevance_evaluations(evaluations, conn=conn)
            job_ids = {
                self._clean_value(decision.get("job_id"))
                for decision in decisions
                if self._clean_value(decision.get("job_id"))
            }
            existing_batch_keys: set[tuple[str, str, str, str]] = set()
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                rows = conn.execute(
                    f"""
                    SELECT job_id, filter_name, decision_action, details_json
                    FROM filter_decisions
                    WHERE job_id IN ({placeholders})
                    """,
                    sorted(job_ids),
                ).fetchall()
                for row in rows:
                    try:
                        details = json.loads(row[3] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        details = {}
                    batch_id = str(details.get("batch_id") or "") if isinstance(details, dict) else ""
                    if batch_id:
                        existing_batch_keys.add((str(row[0]), str(row[1]), str(row[2]), batch_id))

            pending_decisions: list[dict[str, Any]] = []
            for decision in decisions:
                details = decision.get("details")
                batch_id = str(details.get("batch_id") or "") if isinstance(details, dict) else ""
                key = (
                    self._clean_value(decision.get("job_id")),
                    self._clean_value(decision.get("filter_name")),
                    self._clean_value(decision.get("decision_action"), "archive"),
                    batch_id,
                )
                if batch_id and key in existing_batch_keys:
                    continue
                pending_decisions.append(decision)
                if batch_id:
                    existing_batch_keys.add(key)

            recorded = self.record_filter_decisions(pending_decisions, conn=conn)
            jobs_by_reason: dict[str, list[str]] = {}
            for decision in pending_decisions:
                if str(decision.get("decision_action") or "").lower() != "archive":
                    continue
                job_id = self._clean_value(decision.get("job_id"))
                reason = self._clean_value(decision.get("reason"), "Archived by relevance rule")
                if job_id:
                    jobs_by_reason.setdefault(reason, []).append(job_id)
            archived = sum(
                self.archive_jobs(job_ids, archived_reason=reason, conn=conn)
                for reason, job_ids in jobs_by_reason.items()
            )
            restore_ids = [
                self._resolve_stored_job_id(
                    conn,
                    self._clean_value(decision.get("job_id")),
                )
                for decision in pending_decisions
                if str(decision.get("decision_action") or "").lower() == "restore"
                and self._clean_value(decision.get("job_id"))
            ]
            restored = 0
            for start in range(0, len(restore_ids), 500):
                chunk = list(dict.fromkeys(restore_ids[start : start + 500]))
                if not chunk:
                    continue
                placeholders = ",".join("?" for _ in chunk)
                active_conn_ids = [
                    str(row[0])
                    for row in conn.execute(
                        f"SELECT job_id FROM jobs WHERE archived_at IS NOT NULL AND job_id IN ({placeholders})",
                        chunk,
                    )
                ]
                conn.executemany(
                    "UPDATE jobs SET archived_at = NULL, archived_reason = NULL WHERE job_id = ?",
                    [(job_id,) for job_id in active_conn_ids],
                )
                restored += len(active_conn_ids)
            conn.commit()
        return {
            "evaluated": evaluated,
            "decisions_recorded": recorded,
            "archived": archived,
            "restored": restored,
        }

    def put_into_sql(
        self,
        jobs_df: pd.DataFrame,
        *,
        scrape_run_id: Optional[str] = None,
        observed_at: Optional[str] = None,
    ) -> int:
        """Upsert job sightings and return the number of newly discovered jobs."""
        if jobs_df.empty:
            return 0
        if "job_id" not in jobs_df.columns:
            logger.warning("No job_id column found in DataFrame")
            return 0

        default_observed_at = self._normalize_timestamp(observed_at) or utc_now_iso()
        prepared: list[dict[str, str]] = []
        for _, row in jobs_df.iterrows():
            source = self._clean_value(row.get("source"))
            title = self._clean_value(row.get("title"))
            company = self._clean_value(row.get("company"))
            url = self._clean_value(row.get("url"))
            normalized_key = build_normalized_key(
                url=url, source=source, title=title, company=company
            )
            incoming_id = normalize_job_url(row.get("job_id", ""), source)
            if normalized_key.startswith("linkedin:"):
                incoming_id = normalized_key
            incoming_id = incoming_id or normalized_key
            if not incoming_id or not title or not company:
                logger.warning("Skipping job with an incomplete identity: {} at {}", title, company)
                continue
            prepared.append({
                "job_id": incoming_id,
                "title": title,
                "company": company,
                "location": self._clean_value(row.get("location"), "Unknown"),
                "source": source or "LinkedIn",
                "url": url,
                "salary": self._clean_value(row.get("salary"), "Not specified"),
                "normalized_key": normalized_key,
                "observed_at": self._normalize_timestamp(row.get("scraped_at")) or default_observed_at,
            })

        inserted_count = 0
        observed_count = 0
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            identity_index: dict[str, str] = {}
            for row in cursor.execute(
                """SELECT job_id, title, company, source, url, normalized_key FROM jobs
                    ORDER BY CASE WHEN normalized_key LIKE 'linkedin:%' THEN 0 ELSE 1 END, rowid"""
            ):
                for alias in self._job_identity_aliases(
                    job_id=row[0], title=row[1], company=row[2], source=row[3],
                    url=row[4], normalized_key=row[5],
                ):
                    identity_index.setdefault(alias, str(row[0]))

            for job in prepared:
                canonical_id = identity_index.get(job["job_id"]) or identity_index.get(job["normalized_key"])
                if canonical_id is None:
                    for alias in self._job_identity_aliases(
                        job_id=job["job_id"],
                        title=job["title"],
                        company=job["company"],
                        source=job["source"],
                        url=job["url"],
                        normalized_key=job["normalized_key"],
                    ):
                        if alias in identity_index:
                            canonical_id = identity_index[alias]
                            break
                existing = None
                if canonical_id:
                    existing = cursor.execute(
                        """
                        SELECT job_id, title, company, location, source, url, salary,
                               normalized_key, first_seen_at, last_seen_at, seen_count
                        FROM jobs WHERE job_id = ?
                        """,
                        (canonical_id,),
                    ).fetchone()

                canonical_id = str(existing[0]) if existing else job["job_id"]
                if scrape_run_id:
                    observation_exists = cursor.execute(
                        """
                        SELECT 1 FROM job_observations
                        WHERE job_id = ? AND (scrape_run_id = ? OR observed_at = ?)
                        """,
                        (canonical_id, scrape_run_id, job["observed_at"]),
                    ).fetchone() is not None
                else:
                    observation_exists = cursor.execute(
                        "SELECT 1 FROM job_observations WHERE job_id = ? AND observed_at = ?",
                        (canonical_id, job["observed_at"]),
                    ).fetchone() is not None

                if existing is None:
                    cursor.execute(
                        """
                        INSERT INTO jobs (
                            job_id, title, company, location, source, url, salary,
                            normalized_key, scraped_at, first_seen_at, last_seen_at,
                            seen_count, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            canonical_id, job["title"], job["company"], job["location"],
                            job["source"], job["url"], job["salary"], job["normalized_key"],
                            job["observed_at"], job["observed_at"], job["observed_at"],
                            job["observed_at"],
                        ),
                    )
                    inserted_count += 1
                else:
                    cursor.execute(
                        """
                        UPDATE jobs
                        SET title = ?, company = ?, location = ?, source = ?, url = ?,
                            salary = ?, normalized_key = ?, first_seen_at = ?,
                            last_seen_at = ?, seen_count = ?
                        WHERE job_id = ?
                        """,
                        (
                            job["title"] or str(existing[1] or ""),
                            job["company"] or str(existing[2] or ""),
                            job["location"] or str(existing[3] or "Unknown"),
                            job["source"] or str(existing[4] or "LinkedIn"),
                            job["url"] or str(existing[5] or ""),
                            job["salary"] if job["salary"] != "Not specified" else str(existing[6] or "Not specified"),
                            job["normalized_key"] or str(existing[7] or ""),
                            self._pick_earliest_timestamp(existing[8], job["observed_at"]),
                            (
                                self._normalize_timestamp(existing[9])
                                if observation_exists
                                else self._pick_latest_timestamp(existing[9], job["observed_at"])
                            ),
                            int(existing[10] or 1) if observation_exists else int(existing[10] or 1) + 1,
                            canonical_id,
                        ),
                    )

                cursor.execute(
                    """
                    INSERT OR IGNORE INTO job_observations (
                        job_id, scrape_run_id, observed_at, title, company,
                        location, source, url, salary, is_new
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        canonical_id, scrape_run_id, job["observed_at"], job["title"],
                        job["company"], job["location"], job["source"], job["url"],
                        job["salary"], 1 if existing is None else 0,
                    ),
                )
                if not observation_exists:
                    observed_count += 1
                for alias in self._job_identity_aliases(
                    job_id=canonical_id,
                    title=job["title"],
                    company=job["company"],
                    source=job["source"],
                    url=job["url"],
                    normalized_key=job["normalized_key"],
                ):
                    identity_index.setdefault(alias, canonical_id)
            rebuild_consolidation(conn)
            conn.commit()

        if scrape_run_id:
            self.finish_scrape_run(
                scrape_run_id,
                observed_jobs_count=observed_count,
                new_jobs_count=inserted_count,
            )
        logger.info("DB updated with {} new jobs", inserted_count)
        return inserted_count
