from __future__ import annotations

import sqlite3
import unittest
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


if __name__ == "__main__":
    unittest.main()
