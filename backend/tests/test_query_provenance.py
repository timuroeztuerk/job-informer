"""Normalized query provenance and observation-count invariants."""

from __future__ import annotations

import unittest
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from backend.src.utils.database import JobDatabase


class TestQueryProvenance(unittest.TestCase):
    def test_multiple_query_matches_keep_one_observation_per_job_and_run(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            run_id = db.start_scrape_run(
                keywords=["Data Analyst", "Data Scientist"],
                locations=["Berlin"],
                observed_at="2026-09-04T08:00:00Z",
            )
            reports = [
                {
                    "keyword": "Data Analyst",
                    "location": "Berlin",
                    "query_group_key": "analytics_bi",
                    "query_group_name": "Analytics and BI",
                    "page_offsets": [0, 25],
                    "pages_attempted": 2,
                    "pages_completed": 2,
                    "raw_cards": 50,
                    "valid_jobs": 42,
                    "duplicate_cards": 8,
                    "request_attempts": 2,
                    "stop_reason": "page_limit",
                    "last_status": 200,
                },
                {
                    "keyword": "Data Scientist",
                    "location": "Berlin",
                    "query_group_key": "data_science",
                    "query_group_name": "Data science",
                    "page_offsets": [0],
                    "pages_attempted": 1,
                    "pages_completed": 1,
                    "raw_cards": 25,
                    "valid_jobs": 20,
                    "duplicate_cards": 5,
                    "request_attempts": 1,
                    "stop_reason": "short_page",
                    "last_status": 200,
                },
            ]
            query_ids = db.record_collection_queries(run_id, reports)
            self.assertEqual(len(query_ids), 2)

            frame = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:123",
                        "title": "Analytics Engineer",
                        "company": "ACME",
                        "location": "Berlin",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/123/",
                    }
                ]
            )
            self.assertEqual(db.put_into_sql(frame, scrape_run_id=run_id, observed_at="2026-09-04T08:00:00Z"), 1)
            self.assertEqual(db.put_into_sql(frame, scrape_run_id=run_id, observed_at="2026-09-04T08:05:00Z"), 0)

            matches = [
                {
                    "job_id": "linkedin:123",
                    "query_text": "Data Analyst",
                    "query_location": "Berlin",
                    "query_page_offset": 25,
                },
                {
                    "job_id": "linkedin:123",
                    "query_text": "Data Scientist",
                    "query_location": "Berlin",
                    "query_page_offset": 0,
                },
            ]
            self.assertEqual(db.record_job_query_matches(run_id, matches), 2)
            self.assertEqual(db.record_job_query_matches(run_id, matches), 0)

            with db._get_connection() as conn:  # noqa: SLF001
                seen_count, last_seen_at = conn.execute(
                    "SELECT seen_count, last_seen_at FROM jobs WHERE job_id = 'linkedin:123'"
                ).fetchone()
                observations = conn.execute(
                    "SELECT COUNT(*) FROM job_observations WHERE job_id = 'linkedin:123' AND scrape_run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                query_matches = conn.execute(
                    "SELECT COUNT(*) FROM job_query_matches WHERE job_id = 'linkedin:123'"
                ).fetchone()[0]
                coverage = conn.execute(
                    """
                    SELECT pages_attempted, pages_completed, raw_cards, valid_jobs, stop_reason
                    FROM collection_queries
                    WHERE scrape_run_id = ? AND query_text = 'Data Analyst'
                    """,
                    (run_id,),
                ).fetchone()

            self.assertEqual(seen_count, 1)
            self.assertEqual(last_seen_at, "2026-09-04T08:00:00+00:00")
            self.assertEqual(observations, 1)
            self.assertEqual(query_matches, 2)
            self.assertEqual(tuple(coverage), (2, 2, 50, 42, "page_limit"))

    def test_schema_migration_adds_relevance_and_provenance_without_removing_legacy_data(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "jobs.db"
            with sqlite3.connect(path) as conn:
                conn.execute(
                    """
                    CREATE TABLE jobs (
                        job_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        company TEXT NOT NULL,
                        location TEXT NOT NULL,
                        source TEXT NOT NULL,
                        url TEXT,
                        salary TEXT,
                        normalized_key TEXT,
                        scraped_at TIMESTAMP NOT NULL,
                        first_seen_at TIMESTAMP,
                        last_seen_at TIMESTAMP,
                        seen_count INTEGER DEFAULT 1,
                        archived_at TIMESTAMP,
                        archived_reason TEXT,
                        created_at TIMESTAMP NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id, title, company, location, source, scraped_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "legacy-job",
                        "Data Analyst",
                        "ACME",
                        "Berlin",
                        "LinkedIn",
                        "2026-09-01T08:00:00Z",
                        "2026-09-01T08:00:00Z",
                    ),
                )
            db = JobDatabase(path)
            with db._get_connection() as conn:  # noqa: SLF001
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            self.assertTrue(
                {"query_groups", "query_terms", "collection_queries", "job_query_matches"}.issubset(tables)
            )
            self.assertTrue(
                {"relevance_outcome", "role_family", "relevance_reason", "relevance_ruleset_version"}.issubset(columns)
            )
            with db._get_connection() as conn:  # noqa: SLF001
                self.assertEqual(
                    conn.execute("SELECT title FROM jobs WHERE job_id = 'legacy-job'").fetchone()[0],
                    "Data Analyst",
                )


if __name__ == "__main__":
    unittest.main()
