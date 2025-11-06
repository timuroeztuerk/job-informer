"""Comprehensive unit tests for utility helpers.

These tests previously lived across multiple modules. They are now gathered
into a single module so they can be discovered easily by ``python -m unittest``
while still exercising the same behaviours that were covered before.
"""

from __future__ import annotations

import os
import unittest
from tempfile import TemporaryDirectory

import pandas as pd

from . import data_utils


class TestDataCleaningUtilities(unittest.TestCase):
    """Tests covering the data cleaning helpers."""

    def test_clean_job_data_strips_whitespace_and_deduplicates(self) -> None:
        raw = pd.DataFrame(
            [
                {"title": "  Data Scientist  ", "company": "ACME", "location": "Remote"},
                {"title": "Data Scientist", "company": "ACME", "location": "Remote"},
                {"title": "ML Internship", "company": "ACME", "location": "Remote"},
            ]
        )

        cleaned = data_utils.clean_job_data(raw)

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned.iloc[0]["title"], "Data Scientist")
        self.assertNotIn("ML Internship", cleaned["title"].values)

    def test_filter_jobs_by_keywords_is_case_insensitive(self) -> None:
        df = pd.DataFrame(
            [
                {"title": "Senior Data Scientist", "location": "Berlin"},
                {"title": "Junior Developer", "location": "Paris"},
                {"title": "data engineer", "location": "Remote"},
            ]
        )

        filtered = data_utils.filter_jobs_by_keywords(df, ["data"])

        self.assertListEqual(filtered["title"].tolist(), ["Senior Data Scientist", "data engineer"])

    def test_filter_jobs_by_location_handles_multiple_locations(self) -> None:
        df = pd.DataFrame(
            [
                {"title": "Role A", "location": "Berlin, Germany"},
                {"title": "Role B", "location": "Paris, France"},
                {"title": "Role C", "location": "Remote"},
            ]
        )

        filtered = data_utils.filter_jobs_by_location(df, ["berlin", "remote"])

        self.assertEqual(len(filtered), 2)
        self.assertTrue(filtered["location"].str.contains("Berlin").any())
        self.assertTrue(filtered["location"].str.contains("Remote").any())

    def test_add_relevance_score_prioritises_keyword_matches(self) -> None:
        df = pd.DataFrame(
            [
                {"title": "Senior Data Scientist", "company": "ACME"},
                {"title": "Backend Engineer", "company": "Datafy"},
                {"title": "Frontend Developer", "company": "ACME"},
            ]
        )

        # Highest score should be the title containing the keyword
        self.assertEqual(scored.iloc[0]["title"], "Senior Data Scientist")
        self.assertGreater(scored.iloc[0]["relevance_score"], scored.iloc[-1]["relevance_score"])

class TestSummaryAndExportUtilities(unittest.TestCase):
    """Tests focused on summary generation and export helpers."""

    def test_generate_job_summary_populates_expected_keys(self) -> None:
        df = pd.DataFrame(
            {
                "title": ["Role A", "Role B"],
                "company": ["ACME", "ACME"],
                "location": ["Berlin", "Paris"],
                "source": ["LinkedIn", "LinkedIn"],
                "scraped_at": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            }
        )

        summary = data_utils.generate_job_summary(df)

        self.assertEqual(summary["total_jobs"], 2)
        self.assertIn("LinkedIn", summary["sources"])
        self.assertEqual(summary["top_companies"], {"ACME": 2})
        self.assertEqual(summary["top_locations"], {"Berlin": 1, "Paris": 1})
        self.assertEqual(summary["date_range"]["earliest"], "2024-01-01T00:00:00")
        self.assertEqual(summary["date_range"]["latest"], "2024-01-02T00:00:00")

    def test_export_jobs_to_formats_creates_expected_files(self) -> None:
        df = pd.DataFrame(
            {"title": ["Role"], "company": ["ACME"], "location": ["Remote"], "source": ["Site"]}
        )

        with TemporaryDirectory() as tmp_dir:
            old_cwd = os.getcwd()
            os.chdir(tmp_dir)
            try:
                exported = data_utils.export_jobs_to_formats(df, "jobs")
            finally:
                os.chdir(old_cwd)

        # CSV and JSON should always be produced; Excel is optional
        self.assertTrue(any(name.endswith(".csv") for name in exported))
        self.assertTrue(any(name.endswith(".json") for name in exported))


class TestSalaryAndIdentityUtilities(unittest.TestCase):
    """Tests targeting salary parsing and job identity helpers."""

    def test_extract_salary_info_handles_range(self) -> None:
        result = data_utils.extract_salary_info("$50,000 - $70,000 per year")
        self.assertEqual(result["currency"], "$")
        self.assertEqual(result["min_salary"], "50000")
        self.assertEqual(result["max_salary"], "70000")
        self.assertEqual(result["period"], "annual")

    def test_extract_salary_info_handles_missing_values(self) -> None:
        result = data_utils.extract_salary_info("Not specified")
        self.assertIsNone(result["min_salary"])
        self.assertIsNone(result["max_salary"])
        self.assertIsNone(result["currency"])
        self.assertIsNone(result["period"])

    def test_normalize_job_url_prefers_linkedin_identifier(self) -> None:
        url = "https://www.linkedin.com/jobs/view/1234567890/"
        normalized = data_utils.normalize_job_url(url, "LinkedIn")
        self.assertEqual(normalized, "linkedin:1234567890")

    def test_build_job_ids_prefers_normalized_url(self) -> None:
        df = pd.DataFrame(
            {
                "url": ["https://www.linkedin.com/jobs/view/123/"],
                "title": ["Engineer"],
                "company": ["ACME"],
                "source": ["LinkedIn"],
            }
        )

        ids = data_utils.build_job_ids(df)

        self.assertListEqual(ids.tolist(), ["linkedin:123"])

    def test_build_normalized_keys_falls_back_to_title_company_source(self) -> None:
        df = pd.DataFrame(
            {
                "url": [""],
                "title": ["Engineer"],
                "company": ["ACME"],
                "source": ["Indeed"],
            }
        )

        keys = data_utils.build_normalized_keys(df)

        self.assertListEqual(keys.tolist(), ["indeed|engineer|acme"])


if __name__ == "__main__":  # pragma: no cover - module level execution guard
    unittest.main()
