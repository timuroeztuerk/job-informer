"""SQLite lifecycle and integrity regression tests."""

from __future__ import annotations

import gc
import sqlite3
import unittest
import warnings
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.src.utils.database import JobDatabase
from backend.src.utils.sqlite_connection import SQLITE_BUSY_TIMEOUT_MS


class TestDatabaseReliability(unittest.TestCase):
    def test_managed_connections_apply_pragmas_and_close(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(str(Path(tmp_dir) / "jobs.db"))

            with db._get_connection() as conn:  # noqa: SLF001
                self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], SQLITE_BUSY_TIMEOUT_MS)
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_new_orphan_observation_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(str(Path(tmp_dir) / "jobs.db"))

            with self.assertRaisesRegex(sqlite3.IntegrityError, "no matching job"):
                with db._get_connection() as conn:  # noqa: SLF001
                    conn.execute(
                        """
                        INSERT INTO job_observations (
                            job_id, observed_at, title, company, location, source
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        ("missing-job", "2026-07-12T10:00:00", "Role", "ACME", "Berlin", "test"),
                    )

    def test_new_orphan_filter_decision_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(str(Path(tmp_dir) / "jobs.db"))

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

    def test_scrape_run_persists_api_link_and_operational_counts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(str(Path(tmp_dir) / "jobs.db"))
            run_id = db.start_scrape_run(api_run_id="api-123")
            db.finish_scrape_run(
                run_id,
                observed_jobs_count=10,
                new_jobs_count=3,
                archived_jobs_count=2,
                descriptions_fetched_count=4,
                parsed_jobs_count=4,
            )

            with db._get_connection() as conn:  # noqa: SLF001
                row = conn.execute(
                    """
                    SELECT api_run_id, observed_jobs_count, new_jobs_count,
                           archived_jobs_count, descriptions_fetched_count, parsed_jobs_count
                    FROM scrape_runs WHERE scrape_run_id = ?
                    """,
                    (run_id,),
                ).fetchone()

            self.assertEqual(tuple(row), ("api-123", 10, 3, 2, 4, 4))

    def test_legacy_orphans_are_reported_without_being_changed(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "jobs.db"
            JobDatabase(str(db_path))

            # Simulate a pre-guard database. sqlite3 defaults foreign_keys to
            # OFF, as older application connections did.
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("DROP TRIGGER trg_observation_job_exists")
                conn.execute(
                    """
                    INSERT INTO job_observations (
                        job_id, observed_at, title, company, location, source
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("legacy-orphan", "2026-01-01T09:00:00", "Old role", "ACME", "Berlin", "test"),
                )
                conn.commit()

            reopened = JobDatabase(str(db_path))
            report = reopened.get_integrity_report(sample_limit=5)

            self.assertTrue(report["sqlite_ok"])
            self.assertEqual(report["orphan_observation_count"], 1)
            self.assertEqual(report["orphan_observation_job_count"], 1)
            self.assertEqual(report["orphan_observation_samples"][0]["job_id"], "legacy-orphan")

            with closing(sqlite3.connect(db_path)) as conn:
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM job_observations WHERE job_id = 'legacy-orphan'"
                ).fetchone()[0]
            self.assertEqual(remaining, 1)

    def test_integrity_repair_creates_backup_before_removing_orphans(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "jobs.db"
            db = JobDatabase(str(db_path))
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute("DROP TRIGGER trg_observation_job_exists")
                conn.execute("DROP TRIGGER trg_filter_decisions_job_exists")
                conn.execute(
                    """
                    INSERT INTO job_observations (
                        job_id, observed_at, title, company, location, source
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("orphan", "2026-01-01", "Role", "ACME", "Berlin", "test"),
                )
                conn.execute(
                    """
                    INSERT INTO filter_decisions (
                        job_id, decision_source, decision_action, filter_name, reason
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("orphan", "rule", "archive", "test", "legacy"),
                )
                conn.commit()

            with warnings.catch_warnings(record=True) as caught_warnings:
                warnings.simplefilter("always", ResourceWarning)
                result = db.repair_integrity(Path(tmp_dir) / "backups")
                gc.collect()

            unclosed_database_warnings = [
                warning
                for warning in caught_warnings
                if issubclass(warning.category, ResourceWarning)
                and "unclosed database" in str(warning.message)
            ]
            self.assertEqual(unclosed_database_warnings, [])

            backup_path = Path(result["backup_path"])
            self.assertTrue(backup_path.exists())
            self.assertEqual(result["removed"]["job_observations"], 1)
            self.assertEqual(result["removed"]["filter_decisions"], 1)
            self.assertEqual(result["after"]["orphan_record_count"], 0)

            with closing(sqlite3.connect(backup_path)) as backup:
                self.assertEqual(
                    backup.execute("SELECT COUNT(*) FROM job_observations WHERE job_id = 'orphan'").fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
