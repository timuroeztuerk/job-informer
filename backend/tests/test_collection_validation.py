"""Only comparable, complete collections may count toward separate-day evidence."""

from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from backend.api import RunStore
from backend.src.utils.collection_scope import CITY_SEARCH_LOCATIONS, COUNTRYWIDE_SEARCH_LOCATIONS
from backend.src.utils.collection_validation import build_collection_validation
from backend.src.utils.database import JobDatabase
from backend.src.utils.db_summary import build_db_summary

KEYWORDS = ["Data Scientist", "Data Analyst"]
LOCATIONS = [*COUNTRYWIDE_SEARCH_LOCATIONS, *CITY_SEARCH_LOCATIONS]
AS_OF = "2026-09-10T12:00:00Z"


class TestCollectionValidation(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "jobs.db"
        self.db = JobDatabase(self.path)
        RunStore(str(self.path))
        timezone = patch.dict("os.environ", {"APP_TIMEZONE": "Europe/Berlin"})
        timezone.start()
        self.addCleanup(timezone.stop)

    def seed_run(self, run_id, started, *, time_range="day", status="succeeded", query_change=None):
        with self.db._get_connection() as conn:
            conn.execute("""INSERT INTO api_runs
                (run_id, status, started_at, log_path, keywords, locations, time_range)
                VALUES (?, ?, ?, '', ?, ?, ?)""",
                (run_id, status, started, ", ".join(KEYWORDS), ", ".join(LOCATIONS), time_range))
        scrape = self.db.start_scrape_run(api_run_id=run_id, keywords=KEYWORDS, locations=LOCATIONS, observed_at=started)
        reports = [{"keyword": keyword, "location": location, "pages_attempted": 2,
                    "pages_completed": 2, "valid_jobs": 2, "stop_reason": "page_limit"}
                   for keyword, location in product(KEYWORDS, LOCATIONS)]
        if query_change:
            query_change(reports)
        self.db.record_collection_queries(scrape, reports)
        self.db.put_into_sql(pd.DataFrame([
            {"job_id": job_id, "title": "Data Scientist", "company": job_id,
             "location": "Germany", "source": "LinkedIn"}
            for job_id in ["shared", *CITY_SEARCH_LOCATIONS]
        ]), scrape_run_id=scrape, observed_at=started)
        self.db.record_job_query_matches(scrape, [
            {"job_id": job_id, "query_text": report["keyword"], "query_location": report["location"]}
            for report in reports
            for job_id in (["shared", report["location"]] if report["location"] in CITY_SEARCH_LOCATIONS else ["shared"])
        ])

    def report(self):
        with self.db._get_connection() as conn:
            return build_collection_validation(conn, as_of=AS_OF)

    def test_counts_local_days_once_and_compares_matching_search_settings(self):
        self.seed_run("day-1", "2026-09-04T20:00:00Z")
        self.seed_run("day-2", "2026-09-04T23:30:00Z")  # Already September 5 in Berlin.
        self.seed_run("week", "2026-09-05T07:00:00Z", time_range="week")
        self.seed_run("day-2-repeat", "2026-09-05T08:00:00Z")
        with self.db._get_connection() as conn:
            conn.execute("UPDATE api_runs SET keywords='data analyst,Data Scientist', locations=? WHERE run_id='day-1'",
                         (",".join(reversed(LOCATIONS)),))
        report = self.report()
        self.assertEqual(report["healthy_days"], 2)
        self.assertEqual(report["matching_runs"], 3)
        self.assertEqual(report["other_runs"], 1)
        self.assertEqual([row["run_id"] for row in report["runs"] if row["counted"]], ["day-2-repeat", "day-1"])
        self.assertEqual(report["runs"][1]["date"], "2026-09-05")
        self.assertEqual(len(report["cities"]), 4)
        for city in report["cities"]:
            self.assertEqual((city["sampled_days"], city["days_adding_jobs"], city["unique_jobs"],
                              city["shared_jobs"], city["city_only_jobs"]), (2, 2, 4, 2, 2))
        self.assertEqual(report["runs"][0]["city_only_jobs"], 4)
        self.assertEqual(report["runs"][0]["city_coverage_percent"], 20)

    def test_failure_or_incomplete_queries_do_not_count_as_coverage_evidence(self):
        mutations = [
            ("missing", lambda rows: rows.pop(), "Recorded queries"),
            ("partial", lambda rows: rows[0].update(pages_completed=1), "Missing or incomplete"),
            ("failure", lambda rows: rows[0].update(request_failures=1), "Request failures"),
            ("zero", lambda rows: rows[0].update(valid_jobs=0), "returned no jobs"),
            ("stopped", lambda rows: rows[0].update(stop_reason="repeated_page"), "pagination stop"),
        ]
        for index, (run_id, change, _) in enumerate(mutations):
            self.seed_run(run_id, f"2026-09-0{index + 1}T08:00:00Z", query_change=change)
        self.seed_run("failed", "2026-09-06T08:00:00Z", status="failed")
        report = self.report()
        self.assertEqual(report["healthy_days"], 0)
        self.assertEqual(report["cities"], [])
        for run_id, _, concern in mutations:
            row = next(row for row in report["runs"] if row["run_id"] == run_id)
            self.assertIn(concern, " ".join(row["concerns"]))
        self.assertIn("Run has not succeeded", report["runs"][0]["concerns"])

    def test_keeps_earlier_healthy_attempt_when_same_day_retry_fails(self):
        self.seed_run("good", "2026-09-05T08:00:00Z")
        self.seed_run("bad", "2026-09-05T09:00:00Z", status="interrupted")
        report = self.report()
        self.assertEqual(report["healthy_days"], 1)
        self.assertFalse(report["runs"][0]["counted"])
        self.assertTrue(report["runs"][1]["counted"])

    def test_missing_query_provenance_does_not_count_even_when_page_metrics_look_good(self):
        self.seed_run("missing-matches", "2026-09-05T08:00:00Z")
        with self.db._get_connection() as conn:
            conn.execute("DELETE FROM job_query_matches WHERE collection_query_id IN (SELECT collection_query_id FROM collection_queries WHERE location='Berlin')")
        report = self.report()
        self.assertEqual(report["healthy_days"], 0)
        self.assertIn("Job provenance is missing", " ".join(report["runs"][0]["concerns"]))

    def test_summary_evidence_uses_all_collection_days_independent_of_market_window(self):
        self.seed_run("old", "2026-08-01T08:00:00Z")
        self.seed_run("current", "2026-09-05T08:00:00Z")
        self.seed_run("future", "2026-10-05T08:00:00Z")
        recent = build_db_summary(self.db, window="7d", as_of=AS_OF)
        full = build_db_summary(self.db, window="all", as_of=AS_OF)
        self.assertEqual(recent["collection_validation"], full["collection_validation"])
        self.assertEqual(recent["collection_validation"]["healthy_days"], 2)

    def test_empty_legacy_and_partial_scope_do_not_claim_validation_days(self):
        self.assertIsNone(self.report()["baseline"])
        self.seed_run("legacy", "2026-09-04T08:00:00Z", time_range=None)
        self.seed_run("partial-scope", "2026-09-05T08:00:00Z")
        with self.db._get_connection() as conn:
            conn.execute("UPDATE api_runs SET locations='Germany,Berlin' WHERE run_id='partial-scope'")
        self.assertIsNone(self.report()["baseline"])
        self.assertEqual(self.report()["healthy_days"], 0)
        self.assertEqual(self.report()["other_runs"], 2)
