"""Freshness tests for the current-state intelligence view."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.src.utils.database import JobDatabase
from backend.src.utils.db_summary import compute_collection_freshness


def insert_job_and_observation(db: JobDatabase, observed_at: str) -> None:
    with db._get_connection() as conn:  # noqa: SLF001
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, title, company, location, source, scraped_at,
                created_at, first_seen_at, last_seen_at, seen_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "linkedin:freshness",
                "Data Scientist",
                "ACME",
                "Berlin",
                "LinkedIn",
                observed_at,
                observed_at,
                observed_at,
                observed_at,
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO job_observations (
                job_id, observed_at, title, company, location, source
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("linkedin:freshness", observed_at, "Data Scientist", "ACME", "Berlin", "LinkedIn"),
        )


class TestCollectionFreshness(unittest.TestCase):
    def test_empty_database_has_explicit_empty_state(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            freshness = compute_collection_freshness(db, as_of="2026-07-12T12:00:00Z")
            self.assertEqual(freshness["status"], "empty")
            self.assertIsNone(freshness["last_collected_at"])

    def test_fresh_aging_and_stale_thresholds(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            insert_job_and_observation(db, "2026-07-01T12:00:00Z")
            self.assertEqual(compute_collection_freshness(db, as_of="2026-07-03T12:00:00Z")["status"], "fresh")
            self.assertEqual(compute_collection_freshness(db, as_of="2026-07-05T12:00:00Z")["status"], "aging")
            self.assertEqual(compute_collection_freshness(db, as_of="2026-07-09T12:00:00Z")["status"], "stale")

    def test_empty_recent_run_does_not_hide_stale_observations(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            insert_job_and_observation(db, "2026-04-04T09:00:00Z")
            with db._get_connection() as conn:  # noqa: SLF001
                conn.execute(
                    "INSERT INTO scrape_runs (scrape_run_id, mode, observed_at) VALUES (?, ?, ?)",
                    ("empty-run", "run-once", "2026-07-12T08:00:00Z"),
                )
            freshness = compute_collection_freshness(db, as_of="2026-07-12T12:00:00Z")
            self.assertEqual(freshness["status"], "stale")
            self.assertEqual(freshness["source"], "job_observation")
            self.assertEqual(freshness["latest_scrape_at"], "2026-07-12T08:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
