"""Focused tests for the active LinkedIn collection and review product."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pandas as pd

from backend.src.agents.job_scraper import JobScraper
from backend.src.config.settings import DEFAULT_UNWANTED_KEYWORDS
from backend.src.utils.database import JobDatabase
from backend.src.utils.db_summary import build_db_summary
from backend.src.utils.filtering import should_filter_academic_title, should_filter_study_title


FILTER_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "job_filter_cases.json"


def _load_filter_cases() -> list[dict[str, str]]:
    with FILTER_FIXTURE_PATH.open(encoding="utf-8") as fixture_file:
        payload = json.load(fixture_file)
    return payload["cases"]


class TestDeterministicFiltering(unittest.TestCase):
    def test_student_and_academic_roles_are_filtered(self) -> None:
        self.assertTrue(should_filter_study_title("Working Student Data Science"))
        self.assertTrue(should_filter_study_title("Werkstudierende Data Management"))
        self.assertTrue(should_filter_study_title("Werkstudenten Business Development"))
        self.assertTrue(should_filter_study_title("Bachelorand Customer Analytics"))
        self.assertTrue(should_filter_study_title("Master Thesis in AI"))
        self.assertTrue(should_filter_academic_title("PostDoc in Forest Ecology"))
        self.assertTrue(should_filter_academic_title("Professorin für Datenbanken"))
        self.assertTrue(should_filter_academic_title("Wiss. MA - Survey Research"))
        self.assertTrue(should_filter_academic_title("Forschungsassistenz IREM"))

    def test_adjacent_commercial_roles_are_kept(self) -> None:
        self.assertFalse(should_filter_study_title("Software Engineer, AI Platform"))
        self.assertFalse(should_filter_academic_title("Research Scientist at Pharma Company"))

    def test_keep_reject_fixture_covers_ambiguous_role_families(self) -> None:
        cases = _load_filter_cases()
        case_ids = [case["id"] for case in cases]
        categories = {case["category"] for case in cases}
        outcomes = {case["expected"] for case in cases}

        self.assertEqual(len(case_ids), len(set(case_ids)))
        self.assertEqual(outcomes, {"keep", "reject"})
        self.assertTrue(
            {"engineering", "software", "research", "analytics", "project"}.issubset(categories)
        )

    def test_keep_reject_fixture_matches_collection_filter_decisions(self) -> None:
        cases = _load_filter_cases()
        scraper = JobScraper.__new__(JobScraper)
        scraper.config = SimpleNamespace(
            get_unwanted_keywords_list=lambda: DEFAULT_UNWANTED_KEYWORDS.split(","),
            get_unwanted_companies_list=lambda: [],
        )
        jobs = pd.DataFrame(
            {
                "job_id": [case["id"] for case in cases],
                "title": [case["title"] for case in cases],
                "company": [case["company"] for case in cases],
            }
        )

        decisions = scraper._build_filter_decisions_for_jobs(jobs)
        filters_by_job: dict[str, set[str]] = {}
        for decision in decisions:
            filters_by_job.setdefault(str(decision["job_id"]), set()).add(
                str(decision["filter_name"])
            )
            self.assertEqual(decision["decision_action"], "archive")
            self.assertTrue(decision["reason"])
            self.assertTrue(decision["matched_value"])

        for case in cases:
            with self.subTest(case=case["id"], title=case["title"]):
                matched_filters = filters_by_job.get(case["id"], set())
                if case["expected"] == "keep":
                    self.assertEqual(matched_filters, set())
                else:
                    self.assertIn(case["expected_filter"], matched_filters)


class TestCurrentSummary(unittest.TestCase):
    def test_summary_contains_only_broad_current_state_insights(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            db.put_into_sql(
                pd.DataFrame(
                    [
                        {
                            "job_id": "linkedin:1",
                            "title": "Data Scientist",
                            "company": "ACME",
                            "location": "Berlin, Germany",
                            "source": "LinkedIn",
                            "url": "https://www.linkedin.com/jobs/view/1/",
                        },
                        {
                            "job_id": "linkedin:2",
                            "title": "Data Analyst",
                            "company": "ACME",
                            "location": "Munich, Germany",
                            "source": "LinkedIn",
                            "url": "https://www.linkedin.com/jobs/view/2/",
                        },
                    ]
                ),
                observed_at="2026-09-04T08:00:00+00:00",
            )

            summary = build_db_summary(db)

            self.assertEqual(summary["totals"]["total_jobs"], 2)
            self.assertEqual(summary["jobs_by_source"], {"LinkedIn": 2})
            self.assertEqual(summary["top_companies"][0]["name"], "ACME")
            self.assertEqual(summary["top_locations"][0]["name"], "Berlin")
            self.assertNotIn("trend_summary", summary)
            self.assertNotIn("profile_fit_summary", summary)
            self.assertNotIn("parser_telemetry", summary)


if __name__ == "__main__":
    unittest.main()
