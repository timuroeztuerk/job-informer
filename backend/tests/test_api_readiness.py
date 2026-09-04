"""Tests for database readiness and canonical schema initialization."""

from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.api import REQUIRED_DB_TABLES, database_readiness
from backend.src.utils.database import JobDatabase


class TestApiReadiness(unittest.TestCase):
    def test_new_job_database_has_complete_ready_schema(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "jobs.db"
            db = JobDatabase(db_path)
            with db._get_connection() as conn:  # noqa: SLF001
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }

            self.assertTrue(REQUIRED_DB_TABLES.issubset(tables))
            self.assertTrue(
                {
                    "fit_profiles",
                    "job_annotations",
                    "job_fit_scores",
                    "llm_attempts",
                    "parse_job_states",
                    "parsed_descriptions",
                }.isdisjoint(tables)
            )
            readiness = database_readiness(db_path, instance_name="test-instance")
            self.assertEqual(readiness["status"], "ready")
            self.assertEqual(readiness["instance_name"], "test-instance")
            self.assertEqual(readiness["missing_tables"], [])
            self.assertEqual(readiness["total_jobs"], 0)

    def test_incomplete_schema_is_not_ready(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "jobs.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute("CREATE TABLE jobs (job_id TEXT, archived_at TEXT)")

            readiness = database_readiness(db_path)
            self.assertEqual(readiness["status"], "not_ready")
            self.assertFalse(readiness["schema_ok"])
            self.assertIn("job_observations", readiness["missing_tables"])

    def test_legacy_tables_are_left_untouched(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "jobs.db"
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute("CREATE TABLE parsed_descriptions (marker TEXT)")
                conn.execute("INSERT INTO parsed_descriptions VALUES ('keep me')")

            JobDatabase(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                marker = conn.execute("SELECT marker FROM parsed_descriptions").fetchone()[0]
            self.assertEqual(marker, "keep me")

    def test_readiness_does_not_create_a_missing_database(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "missing.db"
            readiness = database_readiness(db_path)

            self.assertEqual(readiness["status"], "not_ready")
            self.assertFalse(db_path.exists())


if __name__ == "__main__":
    unittest.main()
