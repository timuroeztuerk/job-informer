"""
Database utilities for job storage using SQLite
Provides fast deduplication and querying capabilities
"""

import json
import os
import sqlite3
import pandas as pd
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Dict, Any, Iterator
from datetime import datetime, timedelta
from loguru import logger

from .data_utils import build_job_ids, build_normalized_key, normalize_job_url
from .profile_fit import (
    DEFAULT_FIT_PROFILE_ID,
    DEFAULT_FIT_PROFILE_NAME,
    DEFAULT_FIT_PROFILE_VERSION,
    build_job_fit_rows,
    default_fit_profile,
    normalize_fit_profile,
)
from .sqlite_connection import connect_sqlite, open_sqlite

# Default DB relative to backend directory unless overridden by env
DEFAULT_DB_PATH = os.getenv(
    "JOBS_DB_PATH",
    str(Path(__file__).resolve().parents[2] / "data" / "jobs.db"),
)


class JobDatabase:
    """SQLite-based job storage with fast deduplication and querying"""

    ACTIVE_JOBS_WHERE = "archived_at IS NULL"
    
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_data_dir()
        self._init_db()
    
    def _ensure_data_dir(self):
        """Ensure data directory exists"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
    
    def _init_db(self):
        """Initialize database with schema"""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL,
                    source TEXT NOT NULL,
                    url TEXT,
                    salary TEXT,
                    description TEXT,
                    normalized_key TEXT,
                    archived_at TIMESTAMP,
                    archived_reason TEXT,
                    scraped_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_annotations (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'unreviewed',
                    priority TEXT NOT NULL DEFAULT 'medium',
                    notes TEXT NOT NULL DEFAULT '',
                    why_interesting TEXT NOT NULL DEFAULT '',
                    skill_gaps TEXT NOT NULL DEFAULT '[]',
                    follow_up_date TEXT,
                    resume_version TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS scrape_runs (
                    scrape_run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL DEFAULT 'run-once',
                    keywords TEXT,
                    locations TEXT,
                    observed_at TIMESTAMP NOT NULL,
                    observed_jobs_count INTEGER NOT NULL DEFAULT 0,
                    new_jobs_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP
                )
            """)

            conn.execute("""
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
                    description_present INTEGER NOT NULL DEFAULT 0,
                    is_new INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                        ON UPDATE CASCADE ON DELETE RESTRICT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS fit_profiles (
                    profile_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_fit_scores (
                    profile_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    score REAL NOT NULL,
                    band TEXT NOT NULL,
                    reasons_json TEXT NOT NULL DEFAULT '[]',
                    signals_json TEXT NOT NULL DEFAULT '{}',
                    computed_at TIMESTAMP NOT NULL,
                    profile_version INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (profile_id, job_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    entity_hash TEXT,
                    version INTEGER,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    finished_at TIMESTAMP NOT NULL,
                    latency_ms REAL NOT NULL DEFAULT 0,
                    response_id TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    exhausted_retries INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    refusal_text TEXT,
                    error_type TEXT,
                    error_message TEXT,
                    output_preview TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS parse_job_states (
                    job_id TEXT NOT NULL,
                    desc_hash TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TIMESTAMP,
                    last_success_at TIMESTAMP,
                    last_error_type TEXT,
                    last_error_message TEXT,
                    last_refusal_text TEXT,
                    last_response_id TEXT,
                    output_preview TEXT,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (job_id, desc_hash, version)
                )
            """)

            conn.execute("""
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
                    decided_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for fast querying
            self._ensure_jobs_columns(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_company ON jobs(company)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON jobs(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scraped_at ON jobs(scraped_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON jobs(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_archived_at ON jobs(archived_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_status ON job_annotations(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_priority ON job_annotations(priority)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_follow_up_date ON job_annotations(follow_up_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_observations_job ON job_observations(job_id, observed_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_observations_run ON job_observations(scrape_run_id)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_job_seen ON job_observations(job_id, observed_at)")
            # Existing installations predate the foreign key above. This
            # trigger adds the same insert guard without rebuilding the table
            # or touching any legacy orphan rows.
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_observation_job_exists
                BEFORE INSERT ON job_observations
                FOR EACH ROW
                WHEN NOT EXISTS (SELECT 1 FROM jobs WHERE job_id = NEW.job_id)
                BEGIN
                    SELECT RAISE(ABORT, 'job_observations.job_id has no matching job');
                END
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fit_scores_band ON job_fit_scores(profile_id, band)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fit_scores_score ON job_fit_scores(profile_id, score DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_filter_decisions_job ON filter_decisions(job_id, decided_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_filter_decisions_source ON filter_decisions(decision_source, decided_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_attempts_task_type ON llm_attempts(task_type, finished_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_attempts_entity ON llm_attempts(task_type, entity_id, finished_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_parse_job_states_status ON parse_job_states(status, last_attempt_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_parse_job_states_job ON parse_job_states(job_id, last_attempt_at DESC)")

            self._ensure_default_fit_profile(conn)
            conn.commit()
            self._backfill_observation_fields(conn)
            self._backfill_jobs_for_orphaned_parsed_descriptions(conn)
            self._backfill_parse_job_states(conn)

            conn.commit()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        return bool(row and int(row[0]) > 0)

    @staticmethod
    def _get_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        return {str(row[1]) for row in cursor.fetchall()}

    @staticmethod
    def _run_safe_migration(
        conn: sqlite3.Connection,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        label: str,
    ) -> bool:
        try:
            conn.execute("SAVEPOINT observation_backfill")
            conn.execute(sql, params)
            conn.execute("RELEASE SAVEPOINT observation_backfill")
            return True
        except Exception as exc:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT observation_backfill")
                conn.execute("RELEASE SAVEPOINT observation_backfill")
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.warning("Could not {}: {}", label, exc)
            return False

    def _ensure_jobs_columns(self, conn: sqlite3.Connection) -> None:
        """Ensure newer jobs columns exist when opening an older database."""
        try:
            columns = self._get_table_columns(conn, "jobs")
        except Exception:
            return

        migrations = {
            "description": "ALTER TABLE jobs ADD COLUMN description TEXT",
            "normalized_key": "ALTER TABLE jobs ADD COLUMN normalized_key TEXT",
            "analyzed": "ALTER TABLE jobs ADD COLUMN analyzed INTEGER DEFAULT 0",
            "first_seen_at": "ALTER TABLE jobs ADD COLUMN first_seen_at TIMESTAMP",
            "last_seen_at": "ALTER TABLE jobs ADD COLUMN last_seen_at TIMESTAMP",
            "seen_count": "ALTER TABLE jobs ADD COLUMN seen_count INTEGER DEFAULT 1",
            "archived_at": "ALTER TABLE jobs ADD COLUMN archived_at TIMESTAMP",
            "archived_reason": "ALTER TABLE jobs ADD COLUMN archived_reason TEXT",
        }
        for column_name, ddl in migrations.items():
            if column_name in columns:
                continue
            try:
                conn.execute(ddl)
            except Exception as exc:
                logger.warning("Could not add jobs column {}: {}", column_name, exc)

        # Try to create unique index on normalized_key (may fail if duplicates exist)
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_key ON jobs(normalized_key)")
        except Exception as exc:
            logger.warning("Could not create unique index on normalized_key (possible duplicates present): {}", exc)

    def _backfill_observation_fields(self, conn: sqlite3.Connection) -> None:
        """Backfill observation-derived fields for older databases."""
        if not self._run_safe_migration(
            conn,
            """
            UPDATE jobs
            SET
                first_seen_at = COALESCE(first_seen_at, scraped_at, created_at),
                last_seen_at = COALESCE(last_seen_at, scraped_at, created_at),
                seen_count = CASE
                    WHEN seen_count IS NULL OR seen_count < 1 THEN 1
                    ELSE seen_count
                END
            WHERE
                first_seen_at IS NULL
                OR last_seen_at IS NULL
                OR seen_count IS NULL
                OR seen_count < 1
            """,
            label="backfill jobs observation fields",
        ):
            return

        if not self._table_exists(conn, "job_observations"):
            return

        self._run_safe_migration(
            conn,
            """
            INSERT INTO job_observations (
                job_id,
                scrape_run_id,
                observed_at,
                title,
                company,
                location,
                source,
                url,
                salary,
                description_present,
                is_new
            )
            SELECT
                j.job_id,
                NULL,
                COALESCE(j.last_seen_at, j.scraped_at, j.created_at),
                j.title,
                j.company,
                j.location,
                j.source,
                j.url,
                j.salary,
                CASE
                    WHEN COALESCE(TRIM(j.description), '') != '' AND j.description != 'FETCH_FAILED' THEN 1
                    ELSE 0
                END,
                1
            FROM jobs j
            WHERE NOT EXISTS (
                SELECT 1 FROM job_observations o WHERE o.job_id = j.job_id
            )
            """,
            label="backfill historical job observations",
        )

        self._run_safe_migration(
            conn,
            """
            UPDATE jobs
            SET
                first_seen_at = COALESCE(
                    (SELECT MIN(o.observed_at) FROM job_observations o WHERE o.job_id = jobs.job_id),
                    first_seen_at,
                    scraped_at,
                    created_at
                ),
                last_seen_at = COALESCE(
                    (SELECT MAX(o.observed_at) FROM job_observations o WHERE o.job_id = jobs.job_id),
                    last_seen_at,
                    scraped_at,
                    created_at
                ),
                seen_count = COALESCE(
                    (SELECT COUNT(*) FROM job_observations o WHERE o.job_id = jobs.job_id),
                    seen_count,
                    1
                )
            """,
            label="refresh observation aggregates on jobs",
        )

    @staticmethod
    def _infer_source_from_job_id(job_id: str) -> str:
        normalized = (job_id or "").strip().lower()
        if "linkedin" in normalized:
            return "LinkedIn"
        if "indeed" in normalized:
            return "Indeed"
        return "Unknown"

    @staticmethod
    def _infer_url_from_job_id(job_id: str) -> str:
        normalized = (job_id or "").strip()
        if not normalized:
            return ""
        if normalized.startswith(("http://", "https://")):
            return normalized
        if "/" in normalized and "." in normalized.split("/", 1)[0]:
            return f"https://{normalized}"
        return ""

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
        """Return canonical aliases for current and pre-canonical identity fields."""
        aliases: list[str] = []

        def add(value: Any) -> None:
            normalized = normalize_job_url(value, source)
            if normalized and normalized not in aliases:
                aliases.append(normalized)

        # Prefer the key produced by the current canonical path, while also
        # accepting old raw-URL, scheme-less host/path, and job_id values.
        add(normalized_key)
        add(job_id)
        add(url)
        add(
            build_normalized_key(
                url=url,
                source=source,
                title=title,
                company=company,
            )
        )
        return aliases

    @staticmethod
    def _parse_orphaned_payload(payload_json: Any) -> Dict[str, str]:
        fallback = {"location": "Unknown", "description": ""}
        if not payload_json:
            return fallback
        try:
            payload = json.loads(payload_json)
        except Exception:
            return fallback
        if not isinstance(payload, dict):
            return fallback

        location_value = payload.get("location")
        location = "Unknown"
        if isinstance(location_value, list):
            for item in location_value:
                clean = str(item or "").strip()
                if clean:
                    location = clean
                    break
        elif isinstance(location_value, str) and location_value.strip():
            location = location_value.strip()

        summary = str(payload.get("summary") or "").strip()
        return {"location": location or "Unknown", "description": summary}

    def _backfill_jobs_for_orphaned_parsed_descriptions(self, conn: sqlite3.Connection) -> None:
        """Create archived placeholder jobs so old parsed payloads keep a parent row."""
        if not self._table_exists(conn, "parsed_descriptions"):
            return

        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                ranked.job_id,
                ranked.payload_json,
                ranked.created_at,
                latest_obs.title AS observation_title,
                latest_obs.company AS observation_company,
                latest_obs.location AS observation_location,
                latest_obs.source AS observation_source,
                latest_obs.url AS observation_url,
                latest_obs.salary AS observation_salary,
                latest_obs.observed_at AS observation_observed_at,
                COALESCE(obs_counts.seen_count, 0) AS observation_seen_count
            FROM (
                SELECT
                    p.job_id,
                    p.payload_json,
                    p.created_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY p.job_id
                        ORDER BY p.version DESC, datetime(p.created_at) DESC, p.rowid DESC
                    ) AS row_num
                FROM parsed_descriptions p
            ) ranked
            LEFT JOIN jobs j ON j.job_id = ranked.job_id
            LEFT JOIN (
                SELECT
                    ranked_obs.job_id,
                    ranked_obs.title,
                    ranked_obs.company,
                    ranked_obs.location,
                    ranked_obs.source,
                    ranked_obs.url,
                    ranked_obs.salary,
                    ranked_obs.observed_at
                FROM (
                    SELECT
                        o.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY o.job_id
                            ORDER BY datetime(o.observed_at) DESC, o.observation_id DESC
                        ) AS row_num
                    FROM job_observations o
                ) ranked_obs
                WHERE ranked_obs.row_num = 1
            ) latest_obs ON latest_obs.job_id = ranked.job_id
            LEFT JOIN (
                SELECT job_id, COUNT(*) AS seen_count
                FROM job_observations
                GROUP BY job_id
            ) obs_counts ON obs_counts.job_id = ranked.job_id
            WHERE ranked.row_num = 1 AND j.job_id IS NULL
            """
        ).fetchall()

        if not rows:
            return

        insert_rows = []
        for row in rows:
            job_id = str(row["job_id"] or "").strip()
            if not job_id:
                continue

            parsed_payload = self._parse_orphaned_payload(row["payload_json"])
            observed_at = (
                self._normalize_timestamp(row["observation_observed_at"])
                or self._normalize_timestamp(row["created_at"])
                or pd.Timestamp.now().isoformat()
            )
            title = str(row["observation_title"] or "").strip() or f"[Archived placeholder] {job_id}"
            company = str(row["observation_company"] or "").strip() or "Unknown"
            location = str(row["observation_location"] or "").strip() or parsed_payload["location"] or "Unknown"
            source = str(row["observation_source"] or "").strip() or self._infer_source_from_job_id(job_id)
            url = str(row["observation_url"] or "").strip() or self._infer_url_from_job_id(job_id)
            salary = str(row["observation_salary"] or "").strip() or "Not specified"
            description = parsed_payload["description"]
            seen_count = max(1, int(row["observation_seen_count"] or 0))

            insert_rows.append(
                (
                    job_id,
                    title,
                    company,
                    location,
                    source,
                    url,
                    salary,
                    description,
                    job_id,
                    observed_at,
                    "Backfilled archived placeholder for orphaned parsed description",
                    observed_at,
                    observed_at,
                    observed_at,
                    observed_at,
                    seen_count,
                    1,
                )
            )

        if not insert_rows:
            return

        conn.executemany(
            """
            INSERT OR IGNORE INTO jobs (
                job_id,
                title,
                company,
                location,
                source,
                url,
                salary,
                description,
                normalized_key,
                archived_at,
                archived_reason,
                scraped_at,
                created_at,
                first_seen_at,
                last_seen_at,
                seen_count,
                analyzed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_rows,
        )
        logger.info(
            "Backfilled {} archived placeholder job rows for orphaned parsed descriptions",
            len(insert_rows),
        )

    def _backfill_parse_job_states(self, conn: sqlite3.Connection) -> None:
        """Backfill parse status rows for existing parsed payloads."""
        if not self._table_exists(conn, "parsed_descriptions") or not self._table_exists(conn, "parse_job_states"):
            return

        self._run_safe_migration(
            conn,
            """
            INSERT INTO parse_job_states (
                job_id,
                desc_hash,
                version,
                model,
                status,
                attempt_count,
                last_attempt_at,
                last_success_at,
                output_preview,
                updated_at
            )
            SELECT
                p.job_id,
                p.desc_hash,
                p.version,
                p.model,
                'success',
                1,
                COALESCE(p.created_at, CURRENT_TIMESTAMP),
                COALESCE(p.created_at, CURRENT_TIMESTAMP),
                SUBSTR(p.payload_json, 1, 500),
                COALESCE(p.created_at, CURRENT_TIMESTAMP)
            FROM parsed_descriptions p
            WHERE NOT EXISTS (
                SELECT 1
                FROM parse_job_states s
                WHERE s.job_id = p.job_id
                  AND s.desc_hash = p.desc_hash
                  AND s.version = p.version
            )
            """,
            label="backfill parse job states",
        )

    def _ensure_default_fit_profile(self, conn: sqlite3.Connection) -> None:
        """Seed the single-user default fit profile when absent."""
        existing = conn.execute(
            "SELECT profile_id FROM fit_profiles WHERE profile_id = ?",
            (DEFAULT_FIT_PROFILE_ID,),
        ).fetchone()
        if existing:
            return

        profile = default_fit_profile()
        conn.execute(
            """
            INSERT INTO fit_profiles (
                profile_id,
                name,
                config_json,
                version,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                profile["profile_id"],
                profile["name"],
                json.dumps(profile["config"]),
                profile["version"],
            ),
        )

    @staticmethod
    def _normalize_timestamp(value: Any) -> str | None:
        if value in (None, "", "nan", "NaT"):
            return None
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.isoformat()

    @classmethod
    def _pick_earliest_timestamp(cls, *values: Any) -> str | None:
        normalized = [cls._normalize_timestamp(value) for value in values]
        candidates = [value for value in normalized if value]
        if not candidates:
            return None
        return min(candidates)

    @classmethod
    def _pick_latest_timestamp(cls, *values: Any) -> str | None:
        normalized = [cls._normalize_timestamp(value) for value in values]
        candidates = [value for value in normalized if value]
        if not candidates:
            return None
        return max(candidates)

    @staticmethod
    def _serialize_run_payload(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            clean = value.strip()
            return clean or None
        if isinstance(value, (list, tuple, set)):
            values = [str(item).strip() for item in value if str(item).strip()]
            return json.dumps(values) if values else None
        return str(value)

    @staticmethod
    def _chunked(values: list[Any], chunk_size: int = 500) -> list[list[Any]]:
        if not values:
            return []
        return [values[idx: idx + chunk_size] for idx in range(0, len(values), chunk_size)]

    def _active_jobs_clause(self, alias: str = "") -> str:
        prefix = f"{alias}." if alias else ""
        return f"{prefix}{self.ACTIVE_JOBS_WHERE}"
    
    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a configured connection and deterministically close it."""
        with open_sqlite(self.db_path) as conn:
            yield conn

    def record_filter_decisions(
        self,
        decisions: List[Dict[str, Any]],
        *,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """Persist filter/audit decisions for later review."""
        payloads = []
        for decision in decisions:
            job_id = str(decision.get("job_id") or "").strip()
            filter_name = str(decision.get("filter_name") or "").strip()
            reason = str(decision.get("reason") or "").strip()
            if not job_id or not filter_name or not reason:
                continue

            raw_confidence = decision.get("confidence")
            try:
                confidence = None if raw_confidence in (None, "") else float(raw_confidence)
            except (TypeError, ValueError):
                confidence = None

            details = decision.get("details")
            if details is None:
                details_json = "{}"
            elif isinstance(details, str):
                details_json = details.strip() or "{}"
            else:
                try:
                    details_json = json.dumps(details)
                except TypeError:
                    details_json = "{}"

            payloads.append(
                (
                    job_id,
                    str(decision.get("decision_source") or "rule").strip() or "rule",
                    str(decision.get("decision_action") or "archive").strip() or "archive",
                    filter_name,
                    reason,
                    str(decision.get("matched_value") or "").strip() or None,
                    confidence,
                    details_json,
                    self._normalize_timestamp(decision.get("decided_at")) or pd.Timestamp.now().isoformat(),
                )
            )

        if not payloads:
            return 0

        owns_connection = conn is None
        active_conn = conn or connect_sqlite(self.db_path)
        try:
            active_conn.executemany(
                """
                INSERT INTO filter_decisions (
                    job_id,
                    decision_source,
                    decision_action,
                    filter_name,
                    reason,
                    matched_value,
                    confidence,
                    details_json,
                    decided_at
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
        job_ids: List[str],
        *,
        archived_reason: str,
        archived_at: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """Soft-archive jobs instead of deleting them."""
        normalized_job_ids = [str(job_id).strip() for job_id in job_ids if str(job_id).strip()]
        if not normalized_job_ids:
            return 0

        archived_count = 0
        owns_connection = conn is None
        active_conn = conn or connect_sqlite(self.db_path)
        try:
            normalized_archived_at = self._normalize_timestamp(archived_at) or pd.Timestamp.now().isoformat()
            normalized_reason = archived_reason.strip() or "Archived"
            for chunk in self._chunked(normalized_job_ids):
                placeholders = ",".join("?" for _ in chunk)
                active_job_ids = [
                    str(row[0])
                    for row in active_conn.execute(
                        f"SELECT job_id FROM jobs WHERE {self._active_jobs_clause()} AND job_id IN ({placeholders})",
                        chunk,
                    ).fetchall()
                ]
                if not active_job_ids:
                    continue
                active_conn.executemany(
                    """
                    UPDATE jobs
                    SET
                        archived_at = ?,
                        archived_reason = CASE
                            WHEN archived_reason IS NULL OR TRIM(archived_reason) = '' THEN ?
                            ELSE archived_reason
                        END
                    WHERE job_id = ?
                    """,
                    [(normalized_archived_at, normalized_reason, job_id) for job_id in active_job_ids],
                )
                archived_count += len(active_job_ids)

            if owns_connection:
                active_conn.commit()
            return archived_count
        finally:
            if owns_connection:
                active_conn.close()

    @staticmethod
    def _group_job_ids_by_reason(job_reason_map: Dict[str, str], default_reason: str) -> Dict[str, List[str]]:
        grouped: Dict[str, List[str]] = {}
        for job_id, reason in job_reason_map.items():
            normalized_reason = reason.strip() if reason else default_reason
            grouped.setdefault(normalized_reason or default_reason, []).append(job_id)
        return grouped

    def archive_jobs_with_filter_decisions(
        self,
        decisions: List[Dict[str, Any]],
        *,
        default_reason: str = "Archived by filter",
    ) -> Dict[str, int]:
        """Archive jobs and persist the reasons that led to the archive."""
        if not decisions:
            return {"archived": 0, "decisions_recorded": 0}

        job_reason_map: Dict[str, str] = {}
        for decision in decisions:
            job_id = str(decision.get("job_id") or "").strip()
            reason = str(decision.get("reason") or "").strip()
            if job_id and reason and job_id not in job_reason_map:
                job_reason_map[job_id] = reason

        with self._get_connection() as conn:
            decisions_recorded = self.record_filter_decisions(decisions, conn=conn)
            archived_count = 0
            for reason, grouped_job_ids in self._group_job_ids_by_reason(job_reason_map, default_reason).items():
                archived_count += self.archive_jobs(grouped_job_ids, archived_reason=reason, conn=conn)
            conn.commit()

        return {"archived": archived_count, "decisions_recorded": decisions_recorded}

    def restore_job(
        self,
        job_id: str,
        *,
        reason: str = "Job restored for reconsideration",
        decision_source: str = "manual",
    ) -> bool:
        """Restore a previously archived job."""
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return False

        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT archived_at FROM jobs WHERE job_id = ?",
                (normalized_job_id,),
            ).fetchone()
            if row is None:
                return False

            conn.execute(
                """
                UPDATE jobs
                SET archived_at = NULL, archived_reason = NULL, analyzed = 0
                WHERE job_id = ?
                """,
                (normalized_job_id,),
            )
            self.record_filter_decisions(
                [
                    {
                        "job_id": normalized_job_id,
                        "decision_source": decision_source,
                        "decision_action": "restore",
                        "filter_name": "manual_restore",
                        "reason": reason,
                    }
                ],
                conn=conn,
            )
            conn.commit()
        return True

    def get_filter_decisions(self, job_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent filter/audit decisions for a specific job."""
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return []

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                    decision_id,
                    job_id,
                    decision_source,
                    decision_action,
                    filter_name,
                    reason,
                    matched_value,
                    confidence,
                    details_json,
                    decided_at
                FROM filter_decisions
                WHERE job_id = ?
                ORDER BY datetime(decided_at) DESC, decision_id DESC
                LIMIT ?
                """,
                (normalized_job_id, max(1, int(limit))),
            ).fetchall()

        decisions: List[Dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except Exception:
                item["details"] = {}
                item.pop("details_json", None)
            decisions.append(item)
        return decisions

    def record_llm_attempt(
        self,
        *,
        task_type: str,
        entity_id: str,
        model: str,
        status: str,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        latency_ms: float = 0.0,
        entity_hash: Optional[str] = None,
        version: Optional[int] = None,
        response_id: Optional[str] = None,
        retry_count: int = 0,
        exhausted_retries: bool = False,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        refusal_text: Optional[str] = None,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        output_preview: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """Persist one logical LLM invocation attempt."""
        normalized_task_type = str(task_type or "").strip()
        normalized_entity_id = str(entity_id or "").strip()
        normalized_model = str(model or "").strip()
        normalized_status = str(status or "").strip()
        if not normalized_task_type or not normalized_entity_id or not normalized_model or not normalized_status:
            return 0

        metadata_json = "{}"
        if metadata:
            try:
                metadata_json = json.dumps(metadata)
            except TypeError:
                metadata_json = "{}"

        started_ts = self._normalize_timestamp(started_at) or pd.Timestamp.now().isoformat()
        finished_ts = self._normalize_timestamp(finished_at) or started_ts

        owns_connection = conn is None
        active_conn = conn or connect_sqlite(self.db_path)
        try:
            cursor = active_conn.execute(
                """
                INSERT INTO llm_attempts (
                    task_type,
                    entity_id,
                    entity_hash,
                    version,
                    model,
                    status,
                    started_at,
                    finished_at,
                    latency_ms,
                    response_id,
                    retry_count,
                    exhausted_retries,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    refusal_text,
                    error_type,
                    error_message,
                    output_preview,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_task_type,
                    normalized_entity_id,
                    str(entity_hash or "").strip() or None,
                    int(version) if version is not None else None,
                    normalized_model,
                    normalized_status,
                    started_ts,
                    finished_ts,
                    max(0.0, float(latency_ms or 0.0)),
                    str(response_id or "").strip() or None,
                    max(0, int(retry_count or 0)),
                    1 if exhausted_retries else 0,
                    int(input_tokens) if input_tokens is not None else None,
                    int(output_tokens) if output_tokens is not None else None,
                    int(total_tokens) if total_tokens is not None else None,
                    str(refusal_text or "").strip() or None,
                    str(error_type or "").strip() or None,
                    str(error_message or "").strip() or None,
                    str(output_preview or "").strip() or None,
                    metadata_json,
                ),
            )
            if owns_connection:
                active_conn.commit()
            return int(cursor.lastrowid or 0)
        finally:
            if owns_connection:
                active_conn.close()

    def upsert_parse_job_state(
        self,
        *,
        job_id: str,
        desc_hash: str,
        version: int,
        model: str,
        status: str,
        last_attempt_at: Optional[str] = None,
        last_success_at: Optional[str] = None,
        last_error_type: Optional[str] = None,
        last_error_message: Optional[str] = None,
        last_refusal_text: Optional[str] = None,
        last_response_id: Optional[str] = None,
        output_preview: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """Persist the latest parser state for a job/hash/version tuple."""
        normalized_job_id = str(job_id or "").strip()
        normalized_hash = str(desc_hash or "").strip()
        normalized_model = str(model or "").strip()
        normalized_status = str(status or "").strip()
        if not normalized_job_id or not normalized_hash or not normalized_model or not normalized_status:
            return False

        attempt_ts = self._normalize_timestamp(last_attempt_at) or pd.Timestamp.now().isoformat()
        success_ts = self._normalize_timestamp(last_success_at)

        owns_connection = conn is None
        active_conn = conn or connect_sqlite(self.db_path)
        try:
            active_conn.execute(
                """
                INSERT INTO parse_job_states (
                    job_id,
                    desc_hash,
                    version,
                    model,
                    status,
                    attempt_count,
                    last_attempt_at,
                    last_success_at,
                    last_error_type,
                    last_error_message,
                    last_refusal_text,
                    last_response_id,
                    output_preview,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, desc_hash, version) DO UPDATE SET
                    model = excluded.model,
                    status = excluded.status,
                    attempt_count = parse_job_states.attempt_count + 1,
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = CASE
                        WHEN excluded.status = 'success' THEN excluded.last_attempt_at
                        ELSE parse_job_states.last_success_at
                    END,
                    last_error_type = CASE
                        WHEN excluded.status = 'failed' THEN excluded.last_error_type
                        ELSE NULL
                    END,
                    last_error_message = CASE
                        WHEN excluded.status = 'failed' THEN excluded.last_error_message
                        ELSE NULL
                    END,
                    last_refusal_text = CASE
                        WHEN excluded.status = 'refusal' THEN excluded.last_refusal_text
                        ELSE NULL
                    END,
                    last_response_id = excluded.last_response_id,
                    output_preview = excluded.output_preview,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_job_id,
                    normalized_hash,
                    int(version),
                    normalized_model,
                    normalized_status,
                    attempt_ts,
                    success_ts if normalized_status == "success" else None,
                    str(last_error_type or "").strip() or None,
                    str(last_error_message or "").strip() or None,
                    str(last_refusal_text or "").strip() or None,
                    str(last_response_id or "").strip() or None,
                    str(output_preview or "").strip() or None,
                    attempt_ts,
                ),
            )
            if owns_connection:
                active_conn.commit()
            return True
        finally:
            if owns_connection:
                active_conn.close()

    def get_parse_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest parse status for a job."""
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return None

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT
                    job_id,
                    desc_hash,
                    version,
                    model,
                    status,
                    attempt_count,
                    last_attempt_at,
                    last_success_at,
                    last_error_type,
                    last_error_message,
                    last_refusal_text,
                    last_response_id,
                    output_preview,
                    updated_at
                FROM parse_job_states
                WHERE job_id = ?
                ORDER BY version DESC, datetime(COALESCE(last_attempt_at, updated_at)) DESC
                LIMIT 1
                """,
                (normalized_job_id,),
            ).fetchone()

            if row is None and self._table_exists(conn, "parsed_descriptions"):
                row = conn.execute(
                    """
                    SELECT
                        job_id,
                        desc_hash,
                        version,
                        model,
                        'success' AS status,
                        1 AS attempt_count,
                        created_at AS last_attempt_at,
                        created_at AS last_success_at,
                        NULL AS last_error_type,
                        NULL AS last_error_message,
                        NULL AS last_refusal_text,
                        NULL AS last_response_id,
                        SUBSTR(payload_json, 1, 500) AS output_preview,
                        created_at AS updated_at
                    FROM parsed_descriptions
                    WHERE job_id = ?
                    ORDER BY version DESC, datetime(created_at) DESC
                    LIMIT 1
                    """,
                    (normalized_job_id,),
                ).fetchone()

        if row is None:
            return None

        return {
            "job_id": row["job_id"],
            "desc_hash": row["desc_hash"],
            "version": int(row["version"]),
            "model": row["model"],
            "status": row["status"],
            "attempt_count": int(row["attempt_count"] or 0),
            "last_attempt_at": row["last_attempt_at"],
            "last_success_at": row["last_success_at"],
            "last_error_type": row["last_error_type"],
            "last_error_message": row["last_error_message"],
            "last_refusal_text": row["last_refusal_text"],
            "last_response_id": row["last_response_id"],
            "output_preview": row["output_preview"],
            "updated_at": row["updated_at"],
        }

    def get_parser_telemetry_summary(self, *, recent_days: int = 7) -> Dict[str, Any]:
        """Return aggregate parser telemetry for the summary page."""
        summary: Dict[str, Any] = {
            "attempts": 0,
            "success_count": 0,
            "failure_count": 0,
            "refusal_count": 0,
            "recent_window_days": max(1, int(recent_days)),
            "recent_attempts": 0,
            "recent_success_rate": None,
            "average_latency_ms": None,
            "exhausted_retries": 0,
            "jobs_with_current_failed_status": 0,
        }

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            if not self._table_exists(conn, "llm_attempts"):
                return summary

            totals = conn.execute(
                """
                SELECT
                    COUNT(*) AS attempts,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failure_count,
                    SUM(CASE WHEN status = 'refusal' THEN 1 ELSE 0 END) AS refusal_count,
                    AVG(latency_ms) AS average_latency_ms,
                    SUM(CASE WHEN exhausted_retries = 1 THEN 1 ELSE 0 END) AS exhausted_retries
                FROM llm_attempts
                WHERE task_type = 'parser'
                """
            ).fetchone()

            recent = conn.execute(
                """
                SELECT
                    COUNT(*) AS attempts,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count
                FROM llm_attempts
                WHERE task_type = 'parser'
                  AND datetime(finished_at) >= datetime('now', ?)
                """,
                (f"-{summary['recent_window_days']} days",),
            ).fetchone()

            failed_jobs = 0
            if self._table_exists(conn, "parse_job_states"):
                failed_jobs_row = conn.execute(
                    """
                    SELECT COUNT(*) AS failed_jobs
                    FROM (
                        SELECT
                            job_id,
                            status,
                            ROW_NUMBER() OVER (
                                PARTITION BY job_id
                                ORDER BY version DESC, datetime(COALESCE(last_attempt_at, updated_at)) DESC
                            ) AS row_num
                        FROM parse_job_states
                    ) ranked
                    WHERE row_num = 1 AND status = 'failed'
                    """
                ).fetchone()
                failed_jobs = int(failed_jobs_row["failed_jobs"] or 0) if failed_jobs_row else 0

        attempts = int(totals["attempts"] or 0) if totals else 0
        success_count = int(totals["success_count"] or 0) if totals else 0
        failure_count = int(totals["failure_count"] or 0) if totals else 0
        refusal_count = int(totals["refusal_count"] or 0) if totals else 0
        recent_attempts = int(recent["attempts"] or 0) if recent else 0
        recent_successes = int(recent["success_count"] or 0) if recent else 0

        summary.update(
            {
                "attempts": attempts,
                "success_count": success_count,
                "failure_count": failure_count,
                "refusal_count": refusal_count,
                "recent_attempts": recent_attempts,
                "recent_success_rate": (recent_successes / recent_attempts) if recent_attempts else None,
                "average_latency_ms": float(totals["average_latency_ms"]) if totals and totals["average_latency_ms"] is not None else None,
                "exhausted_retries": int(totals["exhausted_retries"] or 0) if totals else 0,
                "jobs_with_current_failed_status": failed_jobs,
            }
        )
        return summary

    def start_scrape_run(
        self,
        *,
        mode: str = "run-once",
        keywords: Any = None,
        locations: Any = None,
        observed_at: Optional[str] = None,
    ) -> str:
        """Create a scrape run record used to group job observations."""
        run_observed_at = self._normalize_timestamp(observed_at) or pd.Timestamp.now().isoformat()
        scrape_run_id = f"scrape_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO scrape_runs (
                    scrape_run_id,
                    mode,
                    keywords,
                    locations,
                    observed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    scrape_run_id,
                    (mode or "run-once").strip() or "run-once",
                    self._serialize_run_payload(keywords),
                    self._serialize_run_payload(locations),
                    run_observed_at,
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
        completed_at: Optional[str] = None,
    ) -> None:
        """Mark a scrape run as complete and persist final counters."""
        if not scrape_run_id:
            return

        updates: list[str] = ["completed_at = ?"]
        params: list[Any] = [self._normalize_timestamp(completed_at) or pd.Timestamp.now().isoformat()]

        if observed_jobs_count is not None:
            updates.append("observed_jobs_count = ?")
            params.append(max(0, int(observed_jobs_count)))
        if new_jobs_count is not None:
            updates.append("new_jobs_count = ?")
            params.append(max(0, int(new_jobs_count)))

        params.append(scrape_run_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE scrape_runs SET {', '.join(updates)} WHERE scrape_run_id = ?",
                params,
            )
            conn.commit()

    def get_fit_profile(self, profile_id: str = DEFAULT_FIT_PROFILE_ID) -> Dict[str, Any]:
        """Return the stored fit profile configuration."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT profile_id, name, config_json, version, created_at, updated_at
                FROM fit_profiles
                WHERE profile_id = ?
                """,
                (profile_id,),
            ).fetchone()

        if row is None:
            profile = default_fit_profile()
            return {
                **profile,
                "created_at": None,
                "updated_at": None,
            }

        return {
            "profile_id": row["profile_id"],
            "name": row["name"],
            "config": normalize_fit_profile(row["config_json"]),
            "version": int(row["version"] or DEFAULT_FIT_PROFILE_VERSION),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def upsert_fit_profile(
        self,
        *,
        profile_id: str = DEFAULT_FIT_PROFILE_ID,
        name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create or update the fit profile, incrementing its version on change."""
        existing = self.get_fit_profile(profile_id)
        next_name = (name or existing.get("name") or DEFAULT_FIT_PROFILE_NAME).strip() or DEFAULT_FIT_PROFILE_NAME
        next_config = normalize_fit_profile(config or existing.get("config"))
        next_version = int(existing.get("version") or DEFAULT_FIT_PROFILE_VERSION) + 1

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO fit_profiles (
                    profile_id,
                    name,
                    config_json,
                    version,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(profile_id) DO UPDATE SET
                    name = excluded.name,
                    config_json = excluded.config_json,
                    version = excluded.version,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    profile_id,
                    next_name,
                    json.dumps(next_config),
                    next_version,
                ),
            )
            conn.commit()

        return self.get_fit_profile(profile_id)

    def recompute_fit_scores(
        self,
        *,
        profile_id: str = DEFAULT_FIT_PROFILE_ID,
        job_ids: Optional[List[str]] = None,
    ) -> int:
        """Compute or refresh fit scores for all or a subset of jobs."""
        normalized_job_ids = [str(job_id).strip() for job_id in (job_ids or []) if str(job_id).strip()]

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            profile_row = conn.execute(
                """
                SELECT profile_id, name, config_json, version, created_at, updated_at
                FROM fit_profiles
                WHERE profile_id = ?
                """,
                (profile_id,),
            ).fetchone()
            profile_record, fit_rows = build_job_fit_rows(
                conn,
                profile_id=profile_id,
                profile_row=profile_row,
                job_ids=normalized_job_ids or None,
            )
            if not fit_rows:
                return 0

            conn.executemany(
                """
                INSERT INTO job_fit_scores (
                    profile_id,
                    job_id,
                    score,
                    band,
                    reasons_json,
                    signals_json,
                    computed_at,
                    profile_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id, job_id) DO UPDATE SET
                    score = excluded.score,
                    band = excluded.band,
                    reasons_json = excluded.reasons_json,
                    signals_json = excluded.signals_json,
                    computed_at = excluded.computed_at,
                    profile_version = excluded.profile_version
                """,
                [
                    (
                        row["profile_id"],
                        row["job_id"],
                        row["score"],
                        row["band"],
                        row["reasons_json"],
                        row["signals_json"],
                        row["computed_at"],
                        row["profile_version"],
                    )
                    for row in fit_rows
                ],
            )
            conn.commit()

        logger.info(
            "Recomputed {} fit score(s) for profile {} (version={})",
            len(fit_rows),
            profile_record["profile_id"],
            profile_record["version"],
        )
        return len(fit_rows)

    def get_job_fit(
        self,
        job_id: str,
        *,
        profile_id: str = DEFAULT_FIT_PROFILE_ID,
    ) -> Optional[Dict[str, Any]]:
        """Return the persisted fit score for a job."""
        if not job_id:
            return None

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT profile_id, job_id, score, band, reasons_json, signals_json, computed_at, profile_version
                FROM job_fit_scores
                WHERE profile_id = ? AND job_id = ?
                """,
                (profile_id, job_id),
            ).fetchone()

        if row is None:
            return None

        try:
            reasons = json.loads(row["reasons_json"])
        except Exception:
            reasons = []
        try:
            signals = json.loads(row["signals_json"])
        except Exception:
            signals = {}

        return {
            "profile_id": row["profile_id"],
            "job_id": row["job_id"],
            "score": float(row["score"]),
            "band": row["band"],
            "reasons": reasons if isinstance(reasons, list) else [],
            "signals": signals if isinstance(signals, dict) else {},
            "computed_at": row["computed_at"],
            "profile_version": int(row["profile_version"] or DEFAULT_FIT_PROFILE_VERSION),
        }

    def get_fit_summary(self, profile_id: str = DEFAULT_FIT_PROFILE_ID) -> Dict[str, Any]:
        """Return an aggregated summary of current fit scores."""
        profile = self.get_fit_profile(profile_id)
        summary: Dict[str, Any] = {
            "profile_id": profile["profile_id"],
            "profile_name": profile["name"],
            "profile_version": profile["version"],
            "total_scored": 0,
            "average_score": None,
            "band_counts": {"high": 0, "medium": 0, "low": 0, "zero": 0},
            "top_jobs": [],
        }

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            score_rows = conn.execute(
                """
                SELECT s.band, COUNT(*) AS count, AVG(s.score) AS avg_score
                FROM job_fit_scores s
                INNER JOIN jobs j ON j.job_id = s.job_id
                WHERE s.profile_id = ? AND j.archived_at IS NULL
                GROUP BY band
                """,
                (profile_id,),
            ).fetchall()
            top_rows = conn.execute(
                """
                SELECT
                    s.job_id,
                    j.title,
                    j.company,
                    s.score,
                    s.band,
                    s.reasons_json
                FROM job_fit_scores s
                INNER JOIN jobs j ON j.job_id = s.job_id
                WHERE s.profile_id = ? AND j.archived_at IS NULL
                ORDER BY s.score DESC, datetime(COALESCE(j.last_seen_at, j.scraped_at, j.created_at)) DESC
                LIMIT 5
                """,
                (profile_id,),
            ).fetchall()

        total_scored = 0
        weighted_scores = 0.0
        for row in score_rows:
            count = int(row["count"] or 0)
            summary["band_counts"][row["band"]] = count
            total_scored += count
            weighted_scores += float(row["avg_score"] or 0.0) * count
        summary["total_scored"] = total_scored
        if total_scored:
            summary["average_score"] = weighted_scores / total_scored

        summary["top_jobs"] = []
        for row in top_rows:
            try:
                reasons = json.loads(row["reasons_json"])
            except Exception:
                reasons = []
            summary["top_jobs"].append(
                {
                    "job_id": row["job_id"],
                    "title": row["title"],
                    "company": row["company"],
                    "score": float(row["score"]),
                    "band": row["band"],
                    "reasons": reasons if isinstance(reasons, list) else [],
                }
            )

        return summary

    def put_into_sql(
        self,
        jobs_df: pd.DataFrame,
        *,
        scrape_run_id: Optional[str] = None,
        observed_at: Optional[str] = None,
    ) -> int:
        """Insert new jobs, ignore duplicates. Returns count of new jobs inserted."""
        if jobs_df.empty:
            return 0
        
        # Ensure job_id column exists
        if 'job_id' not in jobs_df.columns:
            logger.warning("No job_id column found in DataFrame")
            return 0
        
        default_observed_at = self._normalize_timestamp(observed_at) or pd.Timestamp.now().isoformat()
        active_scrape_run_id = scrape_run_id
        auto_created_scrape_run = False
        if not active_scrape_run_id:
            active_scrape_run_id = self.start_scrape_run(mode="database-upsert", observed_at=default_observed_at)
            auto_created_scrape_run = True

        # Prepare data for insertion
        jobs_to_insert = []
        for _, row in jobs_df.iterrows():
            url = str(row.get('url', '')).strip()
            source = str(row.get('source', '')).strip()
            title = str(row.get('title', '')).strip()
            company = str(row.get('company', '')).strip()
            normalized_key = build_normalized_key(
                url=row.get('url', ''),
                source=row.get('source', ''),
                title=row.get('title', ''),
                company=row.get('company', ''),
            )
            incoming_job_id = normalize_job_url(row.get('job_id', ''), source)
            if normalized_key.startswith(('linkedin:', 'indeed:')):
                incoming_job_id = normalized_key
            incoming_job_id = incoming_job_id or normalized_key
            job_data = {
                'job_id': incoming_job_id,
                'title': title,
                'company': company,
                'location': str(row['location']),
                'source': source,
                'url': url,
                'salary': str(row.get('salary', 'Not specified')),
                'description': str(row.get('description', '')),
                'normalized_key': normalized_key,
                'observed_at': self._normalize_timestamp(row.get('scraped_at')) or default_observed_at,
            }
            jobs_to_insert.append(job_data)
        
        # Insert/update jobs and record observations
        inserted_count = 0
        observed_count = 0
        affected_job_ids: set[str] = set()
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Index canonical aliases once per batch. This lets a new
            # linkedin:<id> sighting find a legacy row whose job_id or
            # normalized_key was the old raw/tracked URL, without scanning the
            # table once for every incoming job.
            existing_identity_index: dict[str, str] = {}
            for identity_row in cursor.execute(
                """
                SELECT job_id, title, company, source, url, normalized_key
                FROM jobs
                ORDER BY rowid
                """
            ).fetchall():
                aliases = self._job_identity_aliases(
                    job_id=identity_row[0],
                    title=identity_row[1],
                    company=identity_row[2],
                    source=identity_row[3],
                    url=identity_row[4],
                    normalized_key=identity_row[5],
                )
                for alias in aliases:
                    existing_identity_index.setdefault(alias, str(identity_row[0]))
            
            for job in jobs_to_insert:
                existing = cursor.execute(
                    """
                    SELECT
                        job_id,
                        title,
                        company,
                        location,
                        source,
                        url,
                        salary,
                        description,
                        normalized_key,
                        first_seen_at,
                        last_seen_at,
                        seen_count
                    FROM jobs
                    WHERE job_id = ?
                       OR (
                            normalized_key IS NOT NULL
                            AND normalized_key != ''
                            AND normalized_key = ?
                       )
                    LIMIT 1
                    """,
                    (job['job_id'], job['normalized_key']),
                ).fetchone()

                if existing is None:
                    legacy_job_id = None
                    for alias in self._job_identity_aliases(
                        job_id=job['job_id'],
                        title=job['title'],
                        company=job['company'],
                        source=job['source'],
                        url=job['url'],
                        normalized_key=job['normalized_key'],
                    ):
                        legacy_job_id = existing_identity_index.get(alias)
                        if legacy_job_id:
                            break
                    if legacy_job_id:
                        existing = cursor.execute(
                            """
                            SELECT
                                job_id,
                                title,
                                company,
                                location,
                                source,
                                url,
                                salary,
                                description,
                                normalized_key,
                                first_seen_at,
                                last_seen_at,
                                seen_count
                            FROM jobs
                            WHERE job_id = ?
                            LIMIT 1
                            """,
                            (legacy_job_id,),
                        ).fetchone()

                canonical_job_id = str(existing[0]) if existing else job['job_id']
                is_new = existing is None
                incoming_description = str(job.get('description', '') or '').strip()
                incoming_salary = str(job.get('salary', 'Not specified') or 'Not specified').strip() or 'Not specified'
                observed_job_title = str(job.get('title', '') or '').strip()
                observed_company = str(job.get('company', '') or '').strip()
                observed_location = str(job.get('location', '') or '').strip()
                observed_source = str(job.get('source', '') or '').strip()
                observed_url = str(job.get('url', '') or '').strip()
                job_observed_at = self._normalize_timestamp(job.get('observed_at')) or default_observed_at
                observation_exists = cursor.execute(
                    """
                    SELECT 1
                    FROM job_observations
                    WHERE job_id = ? AND observed_at = ?
                    LIMIT 1
                    """,
                    (canonical_job_id, job_observed_at),
                ).fetchone() is not None

                if is_new:
                    cursor.execute(
                        """
                        INSERT INTO jobs (
                            job_id,
                            title,
                            company,
                            location,
                            source,
                            url,
                            salary,
                            description,
                            normalized_key,
                            scraped_at,
                            first_seen_at,
                            last_seen_at,
                            seen_count
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            canonical_job_id,
                            observed_job_title,
                            observed_company,
                            observed_location,
                            observed_source,
                            observed_url,
                            incoming_salary,
                            incoming_description,
                            job['normalized_key'],
                            job_observed_at,
                            job_observed_at,
                            job_observed_at,
                            1,
                        ),
                    )
                    inserted_count += 1
                else:
                    existing_title = str(existing[1] or '')
                    existing_company = str(existing[2] or '')
                    existing_location = str(existing[3] or '')
                    existing_source = str(existing[4] or '')
                    existing_url = str(existing[5] or '')
                    existing_salary = str(existing[6] or 'Not specified')
                    existing_description = str(existing[7] or '')
                    existing_first_seen = existing[9]
                    existing_last_seen = existing[10]
                    existing_seen_count = int(existing[11] or 1)

                    cursor.execute(
                        """
                        UPDATE jobs
                        SET
                            title = ?,
                            company = ?,
                            location = ?,
                            source = ?,
                            url = ?,
                            salary = ?,
                            description = ?,
                            normalized_key = ?,
                            first_seen_at = ?,
                            last_seen_at = ?,
                            seen_count = ?
                        WHERE job_id = ?
                        """,
                        (
                            observed_job_title or existing_title,
                            observed_company or existing_company,
                            observed_location or existing_location,
                            observed_source or existing_source,
                            observed_url or existing_url,
                            incoming_salary if incoming_salary != 'Not specified' else existing_salary,
                            incoming_description or existing_description,
                            job['normalized_key'] or str(existing[8] or ''),
                            self._pick_earliest_timestamp(existing_first_seen, job_observed_at),
                            self._pick_latest_timestamp(existing_last_seen, job_observed_at),
                            existing_seen_count if observation_exists else max(1, existing_seen_count + 1),
                            canonical_job_id,
                        ),
                    )

                cursor.execute(
                    """
                    INSERT OR IGNORE INTO job_observations (
                        job_id,
                        scrape_run_id,
                        observed_at,
                        title,
                        company,
                        location,
                        source,
                        url,
                        salary,
                        description_present,
                        is_new
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        canonical_job_id,
                        active_scrape_run_id,
                        job_observed_at,
                        observed_job_title,
                        observed_company,
                        observed_location,
                        observed_source,
                        observed_url,
                        incoming_salary,
                        1 if incoming_description and incoming_description != 'FETCH_FAILED' else 0,
                        1 if is_new else 0,
                    ),
                )
                observed_count += 0 if observation_exists else 1
                affected_job_ids.add(canonical_job_id)
                for alias in self._job_identity_aliases(
                    job_id=canonical_job_id,
                    title=observed_job_title,
                    company=observed_company,
                    source=observed_source,
                    url=observed_url,
                    normalized_key=job['normalized_key'],
                ):
                    existing_identity_index.setdefault(alias, canonical_job_id)
            
            conn.commit()

        if active_scrape_run_id:
            self.finish_scrape_run(
                active_scrape_run_id,
                observed_jobs_count=observed_count,
                new_jobs_count=inserted_count,
            )
        if auto_created_scrape_run:
            logger.debug("Observation run {} recorded {} seen jobs", active_scrape_run_id, observed_count)

        if affected_job_ids:
            try:
                self.recompute_fit_scores(job_ids=sorted(affected_job_ids))
            except Exception as exc:
                logger.warning("Could not recompute fit scores after DB upsert: {}", exc)

        logger.info(f"DB Updated with {inserted_count} new jobs.")
        return inserted_count
    
    def get_recent_jobs(self, days: int = 30) -> pd.DataFrame:
        """Get jobs from last N days"""
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        
        with self._get_connection() as conn:
            df = pd.read_sql_query("""
                SELECT * FROM jobs 
                WHERE archived_at IS NULL AND scraped_at > ?
                ORDER BY scraped_at DESC
            """, conn, params=[cutoff_date])
        
        if not df.empty and 'scraped_at' in df.columns:
            df['scraped_at'] = pd.to_datetime(df['scraped_at'])
        
        logger.info(f"Retrieved {len(df)} jobs from last {days} days")
        return df
    
    def get_jobs_by_company(self, company: str) -> pd.DataFrame:
        """Get all jobs from specific company"""
        with self._get_connection() as conn:
            df = pd.read_sql_query("""
                SELECT * FROM jobs 
                WHERE archived_at IS NULL AND company LIKE ?
                ORDER BY scraped_at DESC
            """, conn, params=[f'%{company}%'])
        
        if not df.empty and 'scraped_at' in df.columns:
            df['scraped_at'] = pd.to_datetime(df['scraped_at'])
        
        logger.info(f"Retrieved {len(df)} jobs for company '{company}'")
        return df
    
    def get_jobs_by_source(self, source: str) -> pd.DataFrame:
        """Get all jobs from specific source"""
        with self._get_connection() as conn:
            df = pd.read_sql_query("""
                SELECT * FROM jobs 
                WHERE archived_at IS NULL AND source = ?
                ORDER BY scraped_at DESC
            """, conn, params=[source])
        
        if not df.empty and 'scraped_at' in df.columns:
            df['scraped_at'] = pd.to_datetime(df['scraped_at'])
        
        logger.info(f"Retrieved {len(df)} jobs from source '{source}'")
        return df
    
    def get_job_summary(self) -> Dict:
        """Get summary statistics from database"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Total jobs
            cursor.execute("SELECT COUNT(*) FROM jobs WHERE archived_at IS NULL")
            total_jobs = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM jobs WHERE archived_at IS NOT NULL")
            archived_jobs = cursor.fetchone()[0]
            
            # Jobs by source
            cursor.execute("SELECT source, COUNT(*) FROM jobs WHERE archived_at IS NULL GROUP BY source")
            jobs_by_source = dict(cursor.fetchall())
            
            # Jobs by company (top 10)
            cursor.execute("""
                SELECT company, COUNT(*) as count 
                FROM jobs 
                WHERE archived_at IS NULL
                GROUP BY company 
                ORDER BY count DESC 
                LIMIT 10
            """)
            top_companies = dict(cursor.fetchall())
            
            # Recent activity
            cursor.execute("""
                SELECT COUNT(*) FROM jobs 
                WHERE archived_at IS NULL AND scraped_at > datetime('now', '-7 days')
            """)
            recent_jobs = cursor.fetchone()[0]
            
            # Date range
            cursor.execute("SELECT MIN(scraped_at), MAX(scraped_at) FROM jobs WHERE archived_at IS NULL")
            date_range = cursor.fetchone()

            observation_stats = {}
            try:
                if self._table_exists(conn, 'job_observations'):
                    cursor.execute("SELECT COUNT(*) FROM job_observations")
                    total_observations = cursor.fetchone()[0]

                    cursor.execute("SELECT COUNT(*) FROM jobs WHERE archived_at IS NULL AND COALESCE(seen_count, 1) > 1")
                    repeat_jobs = cursor.fetchone()[0]

                    cursor.execute(
                        "SELECT AVG(COALESCE(seen_count, 1)), MAX(COALESCE(seen_count, 1)) FROM jobs WHERE archived_at IS NULL"
                    )
                    avg_seen_count, max_seen_count = cursor.fetchone()

                    cursor.execute("SELECT COUNT(*) FROM scrape_runs")
                    total_scrape_runs = cursor.fetchone()[0]

                    observation_stats = {
                        'total_observations': total_observations,
                        'repeat_jobs': repeat_jobs,
                        'average_seen_count': float(avg_seen_count) if avg_seen_count is not None else None,
                        'max_seen_count': int(max_seen_count) if max_seen_count is not None else 0,
                        'total_scrape_runs': total_scrape_runs,
                    }
            except Exception as e:
                logger.warning(f"Could not get observation stats: {e}")
            
            # Parsed descriptions statistics
            parsed_descriptions_stats = {}
            try:
                # Check if parsed_descriptions table exists
                cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='parsed_descriptions'")
                if cursor.fetchone()[0] > 0:
                    # Total parsed descriptions
                    cursor.execute("SELECT COUNT(*) FROM parsed_descriptions")
                    total_parsed = cursor.fetchone()[0]

                    # Parsed descriptions tied to current jobs (all versions)
                    cursor.execute("""
                        SELECT COUNT(*) 
                        FROM parsed_descriptions pd
                        INNER JOIN jobs j ON pd.job_id = j.job_id
                        WHERE j.archived_at IS NULL
                    """)
                    active_parsed = cursor.fetchone()[0]

                    # Unique jobs with parsed descriptions (current jobs only)
                    cursor.execute("""
                        SELECT COUNT(DISTINCT pd.job_id)
                        FROM parsed_descriptions pd
                        INNER JOIN jobs j ON pd.job_id = j.job_id
                        WHERE j.archived_at IS NULL
                    """)
                    active_jobs_parsed = cursor.fetchone()[0]

                    # Unique jobs with parsed descriptions (all, including deleted)
                    cursor.execute("SELECT COUNT(DISTINCT job_id) FROM parsed_descriptions")
                    unique_jobs_parsed = cursor.fetchone()[0]
                    
                    # Orphaned parsed descriptions (job_id not in jobs table)
                    cursor.execute("""
                        SELECT COUNT(DISTINCT pd.job_id) 
                        FROM parsed_descriptions pd 
                        LEFT JOIN jobs j ON pd.job_id = j.job_id 
                        WHERE j.job_id IS NULL OR j.archived_at IS NOT NULL
                    """)
                    orphaned_parsed = cursor.fetchone()[0]
                    
                    parsed_descriptions_stats = {
                        'total_parsed_descriptions': total_parsed,
                        'active_parsed_descriptions': active_parsed,
                        'unique_jobs_with_parsed_descriptions': unique_jobs_parsed,
                        'orphaned_parsed_descriptions': orphaned_parsed,
                        'jobs_with_descriptions': active_jobs_parsed
                    }
            except Exception as e:
                logger.warning(f"Could not get parsed descriptions stats: {e}")
            
        return {
            'total_jobs': total_jobs,
            'archived_jobs': archived_jobs,
            'jobs_by_source': jobs_by_source,
            'top_companies': top_companies,
            'recent_jobs_7_days': recent_jobs,
            'date_range': {
                'earliest': date_range[0] if date_range[0] else None,
                'latest': date_range[1] if date_range[1] else None
            },
            'observation_stats': observation_stats,
            'parsed_descriptions_stats': parsed_descriptions_stats
        }

    def get_integrity_report(self, sample_limit: int = 10) -> Dict[str, Any]:
        """Report database corruption and legacy orphan observations.

        This is deliberately read-only: existing orphan rows are surfaced for
        an operator to inspect, never silently deleted or rewritten.
        """
        normalized_limit = max(0, int(sample_limit))
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            quick_check_rows = conn.execute("PRAGMA quick_check").fetchall()
            quick_check_messages = [str(row[0]) for row in quick_check_rows]

            orphan_observation_count = 0
            orphan_observation_job_count = 0
            orphan_samples: list[Dict[str, Any]] = []
            if self._table_exists(conn, "job_observations"):
                counts = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS observation_count,
                        COUNT(DISTINCT o.job_id) AS job_count
                    FROM job_observations o
                    LEFT JOIN jobs j ON j.job_id = o.job_id
                    WHERE j.job_id IS NULL
                    """
                ).fetchone()
                orphan_observation_count = int(counts["observation_count"] or 0)
                orphan_observation_job_count = int(counts["job_count"] or 0)

                if normalized_limit:
                    rows = conn.execute(
                        """
                        SELECT
                            o.job_id,
                            COUNT(*) AS observation_count,
                            MIN(o.observed_at) AS first_observed_at,
                            MAX(o.observed_at) AS last_observed_at
                        FROM job_observations o
                        LEFT JOIN jobs j ON j.job_id = o.job_id
                        WHERE j.job_id IS NULL
                        GROUP BY o.job_id
                        ORDER BY observation_count DESC, o.job_id
                        LIMIT ?
                        """,
                        (normalized_limit,),
                    ).fetchall()
                    orphan_samples = [dict(row) for row in rows]

        return {
            "sqlite_ok": quick_check_messages == ["ok"],
            "sqlite_messages": quick_check_messages,
            "orphan_observation_count": orphan_observation_count,
            "orphan_observation_job_count": orphan_observation_job_count,
            "orphan_observation_samples": orphan_samples,
        }

    def get_job_observations(self, job_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent observation rows for a specific job."""
        if not job_id:
            return []

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                    observed_at,
                    scrape_run_id,
                    title,
                    company,
                    location,
                    source,
                    url,
                    salary,
                    description_present,
                    is_new
                FROM job_observations
                WHERE job_id = ?
                ORDER BY datetime(observed_at) DESC, observation_id DESC
                LIMIT ?
                """,
                (job_id, max(1, int(limit))),
            ).fetchall()

        return [{key: row[key] for key in row.keys()} for row in rows]
    
    def migrate_csv_to_sqlite(self, csv_file: str) -> int:
        """Migrate jobs from CSV file to SQLite database"""
        try:
            df = pd.read_csv(csv_file)
            if 'scraped_at' in df.columns:
                df['scraped_at'] = pd.to_datetime(df['scraped_at'], errors='coerce')
            
            # Build job_ids if not present
            if 'job_id' not in df.columns:
                df = self._ensure_job_ids(df)
            
            inserted = self.put_into_sql(df)
            logger.info(f"Migrated {inserted} jobs from {csv_file}")
            return inserted
            
        except Exception as e:
            logger.error(f"Failed to migrate {csv_file}: {e}")
            return 0
    
    def export_to_csv(self, filename: Optional[str] = None) -> str:
        """Export all jobs to CSV file"""
        if filename is None:
            filename = f"jobs_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        filepath = f"data/{filename}"
        
        with self._get_connection() as conn:
            df = pd.read_sql_query("SELECT * FROM jobs WHERE archived_at IS NULL ORDER BY scraped_at DESC", conn)
        
        if not df.empty and 'scraped_at' in df.columns:
            df['scraped_at'] = pd.to_datetime(df['scraped_at'])
        
        df.to_csv(filepath, index=False)
        logger.info(f"Exported {len(df)} jobs to {filepath}")
        return filepath

    # ======================
    # Description backfilling
    # ======================
    def get_jobs_missing_descriptions(self, limit: int = 100, exclude_failed: bool = True) -> pd.DataFrame:
        """Return jobs that have empty or NULL description, newest first.
        If exclude_failed=True, skip jobs marked as failed (description = 'FETCH_FAILED')."""
        with self._get_connection() as conn:
            if exclude_failed:
                df = pd.read_sql_query(
                    """
                    SELECT job_id, url, source, title, company, scraped_at
                    FROM jobs
                    WHERE archived_at IS NULL
                    AND (description IS NULL OR TRIM(description) = '')
                    AND description != 'FETCH_FAILED'
                    ORDER BY datetime(COALESCE(scraped_at, created_at)) DESC
                    LIMIT ?
                    """,
                    conn,
                    params=[limit]
                )
            else:
                df = pd.read_sql_query(
                    """
                    SELECT job_id, url, source, title, company, scraped_at
                    FROM jobs
                    WHERE archived_at IS NULL
                    AND (description IS NULL OR TRIM(description) = '')
                    ORDER BY datetime(COALESCE(scraped_at, created_at)) DESC
                    LIMIT ?
                    """,
                    conn,
                    params=[limit]
                )
        return df

    def get_jobs_with_masked_titles(self, limit: int = 200) -> pd.DataFrame:
        """Return jobs whose title/company/location looks masked (e.g., asterisks or empty)."""
        with self._get_connection() as conn:
            df = pd.read_sql_query(
                """
                SELECT job_id, url, source, title, company, location, scraped_at, created_at
                FROM jobs
                WHERE archived_at IS NULL AND (
                    title LIKE '%*%' OR company LIKE '%*%' OR location LIKE '%*%' OR
                    TRIM(title) = '' OR TRIM(company) = '' OR TRIM(location) = ''
                )
                ORDER BY datetime(COALESCE(scraped_at, created_at)) DESC
                LIMIT ?
                """,
                conn,
                params=[limit],
            )
        return df

    def reset_failed_descriptions(self) -> int:
        """Reset jobs marked as FETCH_FAILED back to empty string to allow retrying."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute("UPDATE jobs SET description = '' WHERE archived_at IS NULL AND description = 'FETCH_FAILED'")
                conn.commit()
                count = cur.rowcount or 0
                if count > 0:
                    logger.info(f"Reset {count} jobs marked as failed back to empty for retrying")
                return count
            except Exception as e:
                logger.error(f"Failed resetting failed descriptions: {e}")
                return 0

    def get_failed_description_count(self) -> int:
        """Get count of jobs marked as FETCH_FAILED."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM jobs WHERE archived_at IS NULL AND description = 'FETCH_FAILED'")
            return cur.fetchone()[0] or 0

    def mark_description_fetch_failed(self, job_ids: List[str]) -> int:
        """Mark jobs as having failed description fetch to avoid retrying them."""
        if not job_ids:
            return 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.executemany(
                    "UPDATE jobs SET description = 'FETCH_FAILED' WHERE archived_at IS NULL AND job_id = ?",
                    [(job_id,) for job_id in job_ids]
                )
                conn.commit()
                return cur.rowcount or 0
            except Exception as e:
                logger.error(f"Failed marking descriptions as failed: {e}")
                return 0

    def update_job_descriptions(self, updates: List[Dict[str, str]]) -> int:
        """Batch update job descriptions. Each item: {'job_id': ..., 'description': ...}."""
        if not updates:
            return 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.executemany(
                    "UPDATE jobs SET description = ? WHERE archived_at IS NULL AND job_id = ?",
                    [(u['description'], u['job_id']) for u in updates]
                )
                conn.commit()
                return cur.rowcount or 0
            except Exception as e:
                logger.error(f"Failed updating descriptions: {e}")
                return 0

    def update_titles_and_companies(self, updates: List[Dict[str, str]]) -> int:
        """Batch update job titles/companies/locations. Each item requires job_id, title, company, location, normalized_key."""
        if not updates:
            return 0
        with self._get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.executemany(
                    """
                    UPDATE jobs
                    SET title = ?, company = ?, location = ?, normalized_key = ?
                    WHERE job_id = ?
                    """,
                    [
                        (u['title'], u['company'], u.get('location', ''), u.get('normalized_key', ''), u['job_id'])
                        for u in updates
                    ],
                )
                conn.commit()
                return cur.rowcount or 0
            except Exception as e:
                logger.error(f"Failed updating titles/companies: {e}")
                return 0

    def _ensure_job_ids(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure a DataFrame uses the same canonical IDs as database upserts."""
        if df.empty:
            df['job_id'] = pd.Series(dtype='string')
            return df
        df['job_id'] = build_job_ids(df)
        return df

    # ======================
    # Parsed descriptions helpers
    # ======================
    def get_parsed_count(self, version: Optional[int] = None) -> int:
        """Return count of parsed description records, optionally filtered by version."""
        try:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='parsed_descriptions'")
                exists = int(cur.fetchone()[0]) > 0
                if not exists:
                    return 0
                if version is None:
                    cur = conn.execute("SELECT COUNT(*) FROM parsed_descriptions")
                    return int(cur.fetchone()[0])
                cur = conn.execute("SELECT COUNT(*) FROM parsed_descriptions WHERE version = ?", (version,))
                return int(cur.fetchone()[0])
        except Exception:
            return 0
