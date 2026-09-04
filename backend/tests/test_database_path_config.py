"""Tests for explicit and environment-driven database selection."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.src.utils.database import JobDatabase


class TestDatabasePathConfiguration(unittest.TestCase):
    def test_job_database_reads_environment_when_constructed(self) -> None:
        """The database module is already imported before the env value changes."""
        with TemporaryDirectory() as tmp_dir:
            configured_path = Path(tmp_dir) / "late-config" / "jobs.db"

            with patch.dict(os.environ, {"JOBS_DB_PATH": str(configured_path)}, clear=True):
                db = JobDatabase()

            self.assertEqual(Path(db.db_path), configured_path.resolve())
            self.assertTrue(configured_path.exists())

if __name__ == "__main__":
    unittest.main()
