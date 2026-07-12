"""Tests for explicit and environment-driven database selection."""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.src.config.settings import Config
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

    def test_explicit_config_path_controls_integrity_repair_and_backup(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            env_path = root / "repair.env"
            env_path.write_text("JOBS_DB_PATH=state/configured.db\n", encoding="utf-8")
            configured_path = root / "state" / "configured.db"
            stale_environment_path = root / "wrong" / "jobs.db"

            with patch.dict(
                os.environ,
                {"JOBS_DB_PATH": str(stale_environment_path)},
                clear=True,
            ):
                config = Config.from_env(str(env_path))
                self.assertEqual(Path(config.jobs_db_path), configured_path.resolve())
                self.assertEqual(Path(os.environ["JOBS_DB_PATH"]), configured_path.resolve())

                db = JobDatabase(config.jobs_db_path)
                with db._get_connection() as conn:  # noqa: SLF001
                    conn.execute(
                        """
                        INSERT INTO jobs (
                            job_id, title, company, location, source, scraped_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        ("repair-target", "Role", "ACME", "Berlin", "test", "2026-07-12"),
                    )

                original_cwd = Path.cwd()
                original_sys_path = list(sys.path)
                backend_dir = Path(__file__).resolve().parents[1]
                sys.path.insert(0, str(backend_dir))
                try:
                    main_module = importlib.import_module("backend.main")
                finally:
                    os.chdir(original_cwd)
                    sys.path[:] = original_sys_path

                with redirect_stdout(StringIO()):
                    result = main_module.repair_database_integrity(config)

            backup_path = Path(result["backup_path"])
            self.assertEqual(backup_path.parent, (configured_path.parent / "backups").resolve())
            self.assertTrue(backup_path.exists())
            self.assertFalse(stale_environment_path.exists())

            with closing(sqlite3.connect(backup_path)) as backup:
                marker = backup.execute(
                    "SELECT COUNT(*) FROM jobs WHERE job_id = 'repair-target'"
                ).fetchone()[0]
            self.assertEqual(marker, 1)


if __name__ == "__main__":
    unittest.main()
