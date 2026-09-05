"""Conservative role grouping without destroying posting-level evidence."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from backend import api
from backend.src.descriptions.linkedin import FetchedSource
from backend.src.descriptions.service import DescriptionService
from backend.src.utils.database import JobDatabase
from backend.src.utils.db_summary import build_db_summary
from backend.src.utils.job_consolidation import rebuild_consolidation
from backend.tests.test_api_relevance import _list_jobs
from backend.tests.test_descriptions import page


COPY = "Build dependable forecasting models using Python and SQL. Partner with product teams, validate experiments and maintain production pipelines. Explain results to stakeholders and document data quality and modelling assumptions."


class TestJobConsolidation(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = JobDatabase(Path(self.tmp.name) / "jobs.db")
        settings = api.AppSettings(db_path=self.db.db_path, run_store_db_path=self.db.db_path,
            api_token="", max_log_bytes=1000, frontend_dist_dir=Path(self.tmp.name))
        for name, value in [("APP_SETTINGS", settings), ("job_db", self.db)]:
            patcher = patch.object(api, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = DescriptionService(self.db.db_path, interval=0)
        self.addCleanup(self.service.close)

    def add_jobs(self, count=2, **changes):
        frame = pd.DataFrame([dict(job_id=f"linkedin:{1000 + i}", title="Data Scientist",
            company="Deloitte", location=f"City {i}, Germany", source="LinkedIn",
            url=f"https://www.linkedin.com/jobs/view/{1000 + i}/", **changes) for i in range(count)])
        self.db.put_into_sql(frame, observed_at="2026-09-04T08:00:00Z")
        return frame

    def describe(self, number, copy=COPY):
        self.service.fetcher = lambda *_: FetchedSource(200, page(str(number), copy), "text/html", None, None)
        self.service.store.enqueue([f"linkedin:{number}"], refresh=True)
        self.assertTrue(self.service.process_one())
        self.assertFalse(self.service.store.queue_status()["paused"])

    def test_fifteen_cities_become_one_role_with_all_links_and_distinct_run_sightings(self):
        frame = self.add_jobs(15)
        self.assertEqual(_list_jobs()["total"], 15)  # A title alone proves nothing.
        for number in range(1000, 1015):
            self.describe(number)
        self.db.put_into_sql(frame, observed_at="2026-09-05T08:00:00Z")
        result = _list_jobs(location="City 14", location_primary=True)
        self.assertEqual(result["total"], 1)
        job = result["items"][0]
        self.assertEqual(job["posting_count"], 15)
        self.assertEqual(len({p["url"] for p in job["postings"]}), 15)
        self.assertEqual(job["seen_count"], 2)
        self.assertEqual(api.get_job("linkedin:1014", _=True)["job_id"], job["job_id"])
        summary = build_db_summary(self.db, as_of="2026-09-05T10:00:00Z")
        self.assertEqual(summary["totals"]["total_jobs"], 1)
        self.assertEqual(summary["totals"]["locations"], 15)
        self.assertEqual(summary["totals"]["repeated_jobs"], 1)
        with self.db._get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 15)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM job_observations").fetchone()[0], 30)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM description_sources").fetchone()[0], 15)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_different_missing_and_short_descriptions_stay_separate_and_refresh_can_split(self):
        self.add_jobs(3)
        self.describe(1000)
        self.describe(1001, COPY + " German fluency is required.")
        self.describe(1002, "A short snippet.")
        self.assertEqual(_list_jobs()["total"], 3)
        self.describe(1001, COPY.replace(" ", "  \n"))
        self.assertEqual(_list_jobs()["total"], 2)
        self.describe(1001, COPY + " This is a different vacancy.")
        self.assertEqual(_list_jobs()["total"], 3)

    def test_company_and_title_must_both_match(self):
        self.add_jobs(3)
        with self.db._get_connection() as conn:
            conn.execute("UPDATE jobs SET company='Another company' WHERE job_id='linkedin:1001'")
            conn.execute("UPDATE jobs SET title='Senior Data Scientist' WHERE job_id='linkedin:1002'")
        for number in range(1000, 1003):
            self.describe(number)
        self.assertEqual(_list_jobs()["total"], 3)

    def test_flags_favorites_archives_and_restore_cover_the_group(self):
        self.add_jobs()
        self.db.set_job_flag("linkedin:1001", True, "Too much consulting")
        self.db.set_job_favorite("linkedin:1000", True)
        self.db.archive_jobs(["linkedin:1000"], archived_reason="Earlier choice")
        for number in [1000, 1001]:
            self.describe(number)
        job = _list_jobs(flagged=True, favorite=True)["items"][0]
        self.assertEqual(job["flag_reason"], "Too much consulting")
        api.delete_job(job["job_id"], _=True)
        self.assertEqual(_list_jobs()["total"], 0)
        self.assertEqual(_list_jobs(archived="only")["total"], 1)
        api.restore_job("linkedin:1001", _=True)
        self.assertEqual(_list_jobs()["total"], 1)
        self.assertEqual(self.db.get_manual_overrides(), {"linkedin:1000": "keep", "linkedin:1001": "keep"})
        self.db.set_job_flag(job["job_id"], False)
        self.db.set_job_favorite(job["job_id"], False)
        self.assertEqual(_list_jobs(flagged=True)["total"], 0)
        self.assertEqual(_list_jobs(favorite=True)["total"], 0)

    def test_legacy_url_duplicates_are_preserved_and_future_sightings_do_not_create_more(self):
        self.add_jobs(1)
        legacy = "de.linkedin.com/jobs/view/1000"
        with self.db._get_connection() as conn:
            conn.execute("""INSERT INTO jobs (job_id,title,company,location,source,url,normalized_key,
                scraped_at,first_seen_at,last_seen_at) VALUES (?, 'Old title','Deloitte','Berlin',
                'LinkedIn',? ,?, '2026-09-03','2026-09-03','2026-09-03')""", (legacy, "https://" + legacy, legacy))
        JobDatabase(self.db.db_path)
        self.assertEqual(_list_jobs()["total"], 1)
        frame = self.add_jobs(1)
        self.db.put_into_sql(frame, observed_at="2026-09-05T08:00:00Z")
        with self.db._get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 2)
            before = conn.execute("SELECT * FROM job_consolidations").fetchall()
            rebuild_consolidation(conn)
            self.assertEqual(conn.execute("SELECT * FROM job_consolidations").fetchall(), before)
        self.assertEqual(_list_jobs()["items"][0]["seen_count"], 3)
        # Legacy IDs can still fetch the exact, safely normalized posting URL.
        from backend.src.descriptions.linkedin import source_url
        self.assertEqual(source_url(legacy), "https://www.linkedin.com/jobs/view/1000/")
