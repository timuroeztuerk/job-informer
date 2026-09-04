"""Verified SQLite backup and restore tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from backend.src.utils.database import JobDatabase
from backend.src.utils.database_backup import (
    create_database_backup,
    restore_database_backup,
    verify_sqlite_database,
)


class TestDatabaseBackup(unittest.TestCase):
    def test_backup_and_restore_round_trip(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "jobs.db"
            backup_dir = Path(tmp_dir) / "backups"
            db = JobDatabase(database_path)
            db.put_into_sql(
                pd.DataFrame(
                    [{
                        "job_id": "linkedin:1",
                        "title": "Data Scientist",
                        "company": "ACME",
                        "location": "Berlin",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/1/",
                    }]
                ),
                observed_at="2026-09-04T08:00:00Z",
            )

            backup = create_database_backup(database_path, backup_dir=backup_dir)
            verify_sqlite_database(backup)
            db.archive_jobs(["linkedin:1"], archived_reason="Mutation after backup")

            safety_backup = restore_database_backup(backup, database_path)
            self.assertIsNotNone(safety_backup)
            verify_sqlite_database(database_path)
            verify_sqlite_database(safety_backup)
            with db._get_connection() as conn:  # noqa: SLF001
                archived_at = conn.execute(
                    "SELECT archived_at FROM jobs WHERE job_id = 'linkedin:1'"
                ).fetchone()[0]
                integrity = conn.execute("PRAGMA foreign_key_check").fetchall()
            self.assertIsNone(archived_at)
            self.assertEqual(integrity, [])


if __name__ == "__main__":
    unittest.main()
