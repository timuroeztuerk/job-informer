"""SQLite lifecycle and active-schema regression tests."""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.src.utils.database import JobDatabase
from backend.src.utils.sqlite_connection import SQLITE_BUSY_TIMEOUT_MS


class TestDatabaseReliability(unittest.TestCase):
    def test_managed_connections_apply_pragmas_and_close(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")

            with db._get_connection() as conn:  # noqa: SLF001
                self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], SQLITE_BUSY_TIMEOUT_MS)
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_orphan_observation_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "no matching job"):
                with db._get_connection() as conn:  # noqa: SLF001
                    conn.execute(
                        """
                        INSERT INTO job_observations (
                            job_id, observed_at, title, company, location, source
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        ("missing-job", "2026-07-12T10:00:00", "Role", "ACME", "Berlin", "LinkedIn"),
                    )

    def test_orphan_filter_decision_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "no matching job"):
                with db._get_connection() as conn:  # noqa: SLF001
                    conn.execute(
                        """
                        INSERT INTO filter_decisions (
                            job_id, decision_source, decision_action, filter_name, reason
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        ("missing-job", "rule", "archive", "test", "orphan"),
                    )

    def test_scrape_run_persists_counts_and_coverage(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            run_id = db.start_scrape_run(api_run_id="api-123")
            db.finish_scrape_run(
                run_id,
                observed_jobs_count=10,
                new_jobs_count=3,
                archived_jobs_count=2,
                coverage=[
                    {
                        "source": "LinkedIn",
                        "keyword": "Data Scientist",
                        "location": "Berlin",
                        "page_offsets": [0, 25],
                        "pages_attempted": 2,
                        "pages_completed": 2,
                        "stop_reason": "short_page",
                    }
                ],
            )

            with db._get_connection() as conn:  # noqa: SLF001
                row = conn.execute(
                    """
                    SELECT api_run_id, observed_jobs_count, new_jobs_count,
                           archived_jobs_count, coverage_json
                    FROM scrape_runs WHERE scrape_run_id = ?
                    """,
                    (run_id,),
                ).fetchone()

            self.assertEqual(tuple(row[:4]), ("api-123", 10, 3, 2))
            coverage = json.loads(row[4])
            self.assertEqual(coverage[0]["page_offsets"], [0, 25])
            self.assertEqual(coverage[0]["pages_completed"], 2)


if __name__ == "__main__":
    unittest.main()
