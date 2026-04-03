"""
Database utilities for job storage using SQLite
Provides fast deduplication and querying capabilities
"""

import json
import os
import sqlite3
import pandas as pd
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from loguru import logger
import numpy as np

from .profile_fit import (
    DEFAULT_FIT_PROFILE_ID,
    DEFAULT_FIT_PROFILE_NAME,
    DEFAULT_FIT_PROFILE_VERSION,
    build_job_fit_rows,
    default_fit_profile,
    normalize_fit_profile,
)

# Default DB relative to backend directory unless overridden by env
DEFAULT_DB_PATH = os.getenv(
    "JOBS_DB_PATH",
    str(Path(__file__).resolve().parents[2] / "data" / "jobs.db"),
)


class JobDatabase:
    """SQLite-based job storage with fast deduplication and querying"""
    
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_data_dir()
        self._init_db()
    
    def _ensure_data_dir(self):
        """Ensure data directory exists"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
    
    def _init_db(self):
        """Initialize database with schema"""
        with sqlite3.connect(self.db_path) as conn:
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
                    is_new INTEGER NOT NULL DEFAULT 0
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
            
            # Create indexes for fast querying
            conn.execute("CREATE INDEX IF NOT EXISTS idx_company ON jobs(company)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON jobs(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scraped_at ON jobs(scraped_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON jobs(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_status ON job_annotations(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_priority ON job_annotations(priority)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_follow_up_date ON job_annotations(follow_up_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_observations_job ON job_observations(job_id, observed_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_observations_run ON job_observations(scrape_run_id)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_job_seen ON job_observations(job_id, observed_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fit_scores_band ON job_fit_scores(profile_id, band)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fit_scores_score ON job_fit_scores(profile_id, score DESC)")

            self._ensure_jobs_columns(conn)
            self._ensure_default_fit_profile(conn)
            conn.commit()
            self._backfill_observation_fields(conn)

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
    
    def _get_connection(self):
        """Get database connection"""
        return sqlite3.connect(self.db_path)

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

        with sqlite3.connect(self.db_path) as conn:
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
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE scrape_runs SET {', '.join(updates)} WHERE scrape_run_id = ?",
                params,
            )
            conn.commit()

    def get_fit_profile(self, profile_id: str = DEFAULT_FIT_PROFILE_ID) -> Dict[str, Any]:
        """Return the stored fit profile configuration."""
        with sqlite3.connect(self.db_path) as conn:
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

        with sqlite3.connect(self.db_path) as conn:
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

        with sqlite3.connect(self.db_path) as conn:
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

        with sqlite3.connect(self.db_path) as conn:
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

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            score_rows = conn.execute(
                """
                SELECT band, COUNT(*) AS count, AVG(score) AS avg_score
                FROM job_fit_scores
                WHERE profile_id = ?
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
                WHERE s.profile_id = ?
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
            # Build normalized_key similar to build_normalized_keys for a single row
            url = str(row.get('url', '')).strip()
            source = str(row.get('source', '')).strip()
            if url:
                normalized_key = str(url)
            else:
                title = str(row.get('title', '')).strip().lower()
                company = str(row.get('company', '')).strip().lower()
                normalized_key = f"{source.lower()}|{title}|{company}"
            job_data = {
                'job_id': str(row['job_id']),
                'title': str(row['title']),
                'company': str(row['company']),
                'location': str(row['location']),
                'source': str(row['source']),
                'url': str(row.get('url', '')),
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
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
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
                       OR (normalized_key IS NOT NULL AND normalized_key = ?)
                    LIMIT 1
                    """,
                    (job['job_id'], job['normalized_key']),
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
        
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("""
                SELECT * FROM jobs 
                WHERE scraped_at > ?
                ORDER BY scraped_at DESC
            """, conn, params=[cutoff_date])
        
        if not df.empty and 'scraped_at' in df.columns:
            df['scraped_at'] = pd.to_datetime(df['scraped_at'])
        
        logger.info(f"Retrieved {len(df)} jobs from last {days} days")
        return df
    
    def get_jobs_by_company(self, company: str) -> pd.DataFrame:
        """Get all jobs from specific company"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("""
                SELECT * FROM jobs 
                WHERE company LIKE ?
                ORDER BY scraped_at DESC
            """, conn, params=[f'%{company}%'])
        
        if not df.empty and 'scraped_at' in df.columns:
            df['scraped_at'] = pd.to_datetime(df['scraped_at'])
        
        logger.info(f"Retrieved {len(df)} jobs for company '{company}'")
        return df
    
    def get_jobs_by_source(self, source: str) -> pd.DataFrame:
        """Get all jobs from specific source"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("""
                SELECT * FROM jobs 
                WHERE source = ?
                ORDER BY scraped_at DESC
            """, conn, params=[source])
        
        if not df.empty and 'scraped_at' in df.columns:
            df['scraped_at'] = pd.to_datetime(df['scraped_at'])
        
        logger.info(f"Retrieved {len(df)} jobs from source '{source}'")
        return df
    
    def get_job_summary(self) -> Dict:
        """Get summary statistics from database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Total jobs
            cursor.execute("SELECT COUNT(*) FROM jobs")
            total_jobs = cursor.fetchone()[0]
            
            # Jobs by source
            cursor.execute("SELECT source, COUNT(*) FROM jobs GROUP BY source")
            jobs_by_source = dict(cursor.fetchall())
            
            # Jobs by company (top 10)
            cursor.execute("""
                SELECT company, COUNT(*) as count 
                FROM jobs 
                GROUP BY company 
                ORDER BY count DESC 
                LIMIT 10
            """)
            top_companies = dict(cursor.fetchall())
            
            # Recent activity
            cursor.execute("""
                SELECT COUNT(*) FROM jobs 
                WHERE scraped_at > datetime('now', '-7 days')
            """)
            recent_jobs = cursor.fetchone()[0]
            
            # Date range
            cursor.execute("SELECT MIN(scraped_at), MAX(scraped_at) FROM jobs")
            date_range = cursor.fetchone()

            observation_stats = {}
            try:
                if self._table_exists(conn, 'job_observations'):
                    cursor.execute("SELECT COUNT(*) FROM job_observations")
                    total_observations = cursor.fetchone()[0]

                    cursor.execute("SELECT COUNT(*) FROM jobs WHERE COALESCE(seen_count, 1) > 1")
                    repeat_jobs = cursor.fetchone()[0]

                    cursor.execute("SELECT AVG(COALESCE(seen_count, 1)), MAX(COALESCE(seen_count, 1)) FROM jobs")
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
                    """)
                    active_parsed = cursor.fetchone()[0]

                    # Unique jobs with parsed descriptions (current jobs only)
                    cursor.execute("""
                        SELECT COUNT(DISTINCT pd.job_id)
                        FROM parsed_descriptions pd
                        INNER JOIN jobs j ON pd.job_id = j.job_id
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
                        WHERE j.job_id IS NULL
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

    def get_job_observations(self, job_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent observation rows for a specific job."""
        if not job_id:
            return []

        with sqlite3.connect(self.db_path) as conn:
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
        
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("SELECT * FROM jobs ORDER BY scraped_at DESC", conn)
        
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
        with sqlite3.connect(self.db_path) as conn:
            if exclude_failed:
                df = pd.read_sql_query(
                    """
                    SELECT job_id, url, source, title, company, scraped_at
                    FROM jobs
                    WHERE (description IS NULL OR TRIM(description) = '')
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
                    WHERE description IS NULL OR TRIM(description) = ''
                    ORDER BY datetime(COALESCE(scraped_at, created_at)) DESC
                    LIMIT ?
                    """,
                    conn,
                    params=[limit]
                )
        return df

    def get_jobs_with_masked_titles(self, limit: int = 200) -> pd.DataFrame:
        """Return jobs whose title/company/location looks masked (e.g., asterisks or empty)."""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(
                """
                SELECT job_id, url, source, title, company, location, scraped_at, created_at
                FROM jobs
                WHERE (
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
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            try:
                cur.execute("UPDATE jobs SET description = '' WHERE description = 'FETCH_FAILED'")
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
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM jobs WHERE description = 'FETCH_FAILED'")
            return cur.fetchone()[0] or 0

    def mark_description_fetch_failed(self, job_ids: List[str]) -> int:
        """Mark jobs as having failed description fetch to avoid retrying them."""
        if not job_ids:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            try:
                cur.executemany(
                    "UPDATE jobs SET description = 'FETCH_FAILED' WHERE job_id = ?",
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
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            try:
                cur.executemany(
                    "UPDATE jobs SET description = ? WHERE job_id = ?",
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
        with sqlite3.connect(self.db_path) as conn:
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
        """Ensure DataFrame contains a vectorized job_id column"""
        if df.empty:
            df['job_id'] = pd.Series(dtype='string')
            return df
        df_clean = df.copy()
        for col in ['url', 'title', 'company', 'location', 'source']:
            if col in df_clean.columns:
                df_clean[col] = df_clean[col].astype(str).str.strip().str.lower()
            else:
                df_clean[col] = ''
        url_available = df_clean['url'].str.len() > 0
        fallback_id = (df_clean['title'] + '|' + df_clean['company'] + '|' + df_clean['location'] + '|' + df_clean['source'])
        job_ids = np.where(url_available, df_clean['url'], fallback_id)
        df['job_id'] = pd.Series(job_ids, index=df.index)
        return df

    # ======================
    # Parsed descriptions helpers
    # ======================
    def get_parsed_count(self, version: Optional[int] = None) -> int:
        """Return count of parsed description records, optionally filtered by version."""
        try:
            with sqlite3.connect(self.db_path) as conn:
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
