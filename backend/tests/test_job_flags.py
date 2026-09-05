"""Personal mismatch examples survive collection and archive transitions."""

import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
from fastapi import HTTPException
from pydantic import ValidationError

from backend import api
from backend.src.utils.database import JobDatabase
from backend.src.utils.relevance import RULESET_VERSION
from backend.src.utils.relevance_service import apply_relevance_preview, build_relevance_preview


class TestJobFlags(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "jobs.db"
        self.db = JobDatabase(self.path)
        self.jobs = pd.DataFrame([
            {"job_id": "linkedin:flag/example", "title": "Sales Analyst", "company": "ACME",
             "location": "Berlin", "source": "LinkedIn", "url": "https://www.linkedin.com/jobs/view/flag-example/"},
            {"job_id": "linkedin:other", "title": "Data Scientist", "company": "Other",
             "location": "Berlin", "source": "LinkedIn", "url": "https://www.linkedin.com/jobs/view/other/"},
        ])
        self.db.put_into_sql(self.jobs, observed_at="2026-09-05T08:00:00Z")
        settings = api.AppSettings(db_path=str(self.path), run_store_db_path=str(self.path), api_token="",
                                   max_log_bytes=30000, frontend_dist_dir=Path(self.tmp.name) / "dist")
        self.settings_patch = patch.object(api, "APP_SETTINGS", settings)
        self.db_patch = patch.object(api, "job_db", self.db)
        self.settings_patch.start()
        self.db_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.addCleanup(self.db_patch.stop)

    def listing(self, **overrides):
        args = dict(limit=50, offset=0, favorite=False, archived="exclude", date_from=None,
                    date_to=None, sort="scraped_at_desc", _=True)
        return api.list_jobs(**{**args, **overrides})

    def flag(self, **kwargs):
        return api.update_job_flag("linkedin:flag/example", api.FlagUpdate(**kwargs), _=True)

    def test_flag_reason_persists_without_changing_filter_or_favorite_decisions(self):
        original = api.get_job("linkedin:flag/example", _=True)
        self.assertIs(original["is_flagged"], False)
        flagged = self.flag(is_flagged=True)
        self.assertIs(flagged["is_flagged"], True)
        self.assertTrue(flagged["flagged_at"])
        saved = self.flag(is_flagged=True, flag_reason="  Mostly sales, not analytics.  ")
        self.assertEqual(saved["flag_reason"], "Mostly sales, not analytics.")
        self.assertEqual(saved["flagged_at"], flagged["flagged_at"])
        self.assertEqual(self.flag(is_flagged=True), saved)

        # A later sighting and reopening the database must retain the personal example.
        self.db.put_into_sql(self.jobs, observed_at="2026-09-06T08:00:00Z")
        JobDatabase(self.path)
        detail = api.get_job("linkedin:flag/example", _=True)
        self.assertEqual(detail["flag_reason"], saved["flag_reason"])
        self.assertIsNone(detail["archived_at"])
        self.assertIs(detail["is_favorite"], False)
        self.assertEqual(detail["relevance_outcome"], original["relevance_outcome"])
        self.assertEqual(detail["seen_count"], 2)
        self.assertEqual(self.listing()["total"], 2)
        self.assertEqual(self.listing(flagged=True)["items"][0]["flag_reason"], saved["flag_reason"])
        self.assertEqual(self.listing(flagged=True)["total"], 1)

        api.delete_job("linkedin:flag/example", _=True)
        self.assertEqual(self.listing(flagged=True)["total"], 0)
        self.assertEqual(self.listing(flagged=True, archived="include")["total"], 1)
        api.restore_job("linkedin:flag/example", _=True)
        self.assertEqual(self.listing(flagged=True)["total"], 1)
        self.assertIs(api.get_job("linkedin:flag/example", _=True)["is_flagged"], True)
        self.assertEqual(self.flag(is_flagged=False), {
            "job_id": "linkedin:flag/example", "is_flagged": False, "flag_reason": None, "flagged_at": None,
        })
        self.assertEqual(self.listing(flagged=True)["total"], 0)

    def test_missing_job_and_oversized_reason_are_rejected(self):
        with self.assertRaises(HTTPException) as error:
            api.update_job_flag("linkedin:missing", api.FlagUpdate(is_flagged=True), _=True)
        self.assertEqual(error.exception.status_code, 404)
        with self.assertRaises(ValidationError):
            api.FlagUpdate(is_flagged=True, flag_reason="x" * 2001)

    def test_reviewed_filters_preserve_examples_and_honor_restoring_a_job(self):
        self.jobs.loc[0, "title"] = "Senior Data Analyst SAP"
        self.db.put_into_sql(self.jobs, observed_at="2026-09-06T08:00:00Z")
        self.flag(is_flagged=True, flag_reason="No SAP")
        self.db.set_job_favorite("linkedin:flag/example", True)
        preview = build_relevance_preview(self.db)
        self.assertEqual(preview["would_archive"], 1)
        self.assertEqual(self.listing(flagged=True)["total"], 1)  # Preview is read-only.
        apply_relevance_preview(self.db, preview)
        apply_relevance_preview(self.db, preview)  # Same batch stays idempotent.

        detail = api.get_job("linkedin:flag/example", _=True)
        self.assertIsNotNone(detail["archived_at"])
        self.assertTrue(detail["is_flagged"])
        self.assertTrue(detail["is_favorite"])
        self.assertEqual(detail["flag_reason"], "No SAP")
        self.assertEqual(detail["seen_count"], 2)
        self.assertEqual(self.listing(flagged=True, archived="only")["total"], 1)
        with self.db._get_connection() as conn:
            rows = conn.execute(
                "SELECT details_json FROM filter_decisions WHERE job_id = ? AND decision_action = 'archive'",
                ("linkedin:flag/example",),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        details = json.loads(rows[0][0])
        self.assertEqual(details["negative_matches"], ["personal_sap"])
        self.assertEqual(details["positive_matches"], ["analytics_bi"])
        self.assertEqual(details["ruleset_version"], RULESET_VERSION)

        api.restore_job("linkedin:flag/example", _=True)
        self.db.put_into_sql(self.jobs, observed_at="2026-09-07T08:00:00Z")
        preview = build_relevance_preview(self.db)
        self.assertEqual(preview["would_archive"], 0)
        apply_relevance_preview(self.db, preview)
        restored = self.listing(flagged=True)["items"][0]
        self.assertIsNone(restored["archived_at"])
        self.assertEqual(restored["relevance_outcome"], "manual_keep")
        self.assertEqual(restored["flag_reason"], "No SAP")
        self.assertTrue(restored["is_favorite"])

    def test_existing_database_receives_empty_flags_without_changing_jobs(self):
        with self.db._get_connection() as conn:
            conn.execute("DROP VIEW review_jobs")
            for column in ["is_flagged", "flag_reason", "flagged_at"]:
                conn.execute(f"ALTER TABLE jobs DROP COLUMN {column}")
            conn.commit()
        JobDatabase(self.path)
        self.assertEqual(self.listing()["total"], 2)
        self.assertEqual(self.listing(flagged=True)["total"], 0)
        self.assertIs(api.get_job("linkedin:flag/example", _=True)["is_flagged"], False)
