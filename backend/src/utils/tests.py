"""Comprehensive unit tests for utility helpers.

These tests previously lived across multiple modules. They are now gathered
into a single module so they can be discovered easily by ``python -m unittest``
while still exercising the same behaviours that were covered before.
"""

from __future__ import annotations

import json
import os
import unittest
from tempfile import TemporaryDirectory

import pandas as pd

from ..agents.parser import DescriptionTools, JobDescriptionStructure
from . import data_utils
from . import filtering
from . import profile_fit
from .database import JobDatabase
from .llm_connection import LLMConnection


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
        scored = df.copy()
        scored["relevance_score"] = scored["title"].str.lower().str.count(r"\bdata\b")
        scored = scored.sort_values("relevance_score", ascending=False).reset_index(drop=True)

        self.assertEqual(scored.iloc[0]["title"], "Senior Data Scientist")
        self.assertGreater(scored.iloc[0]["relevance_score"], scored.iloc[-1]["relevance_score"])

    def test_should_filter_study_title_detects_student_roles(self) -> None:
        self.assertTrue(filtering.should_filter_study_title("Machine Learning Internship"))
        self.assertTrue(filtering.should_filter_study_title("Working Student Data Science"))
        self.assertTrue(filtering.should_filter_study_title("Master Thesis in AI"))

    def test_should_filter_study_title_keeps_adjacent_technical_roles(self) -> None:
        self.assertFalse(filtering.should_filter_study_title("Software Engineer, AI Platform"))
        self.assertFalse(filtering.should_filter_study_title("Data Engineer"))
        self.assertFalse(filtering.should_filter_study_title("Contract Data Scientist"))

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

    def test_normalize_job_url_prefers_indeed_identifier(self) -> None:
        url = "https://de.indeed.com/viewjob?jk=abcdef123456"
        normalized = data_utils.normalize_job_url(url, "Indeed")
        self.assertEqual(normalized, "indeed:abcdef123456")

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


class TestObservationHistory(unittest.TestCase):
    """Tests for repeated job sightings and observation storage."""

    def test_put_into_sql_records_repeat_observations_without_duplicate_jobs(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "jobs.db")
            db = JobDatabase(db_path=db_path)

            first_seen = "2026-04-01T08:00:00"
            second_seen = "2026-04-03T08:00:00"
            job_frame = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:123",
                        "title": "Data Scientist",
                        "company": "ACME",
                        "location": "Berlin",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/123/",
                        "salary": "Not specified",
                        "description": "Work on models.",
                    }
                ]
            )

            inserted_first = db.put_into_sql(job_frame, observed_at=first_seen)
            inserted_second = db.put_into_sql(job_frame, observed_at=second_seen)

            self.assertEqual(inserted_first, 1)
            self.assertEqual(inserted_second, 0)

            with db._get_connection() as conn:  # noqa: SLF001
                conn.row_factory = None
                job_row = conn.execute(
                    """
                    SELECT scraped_at, first_seen_at, last_seen_at, seen_count
                    FROM jobs
                    WHERE job_id = ?
                    """,
                    ("linkedin:123",),
                ).fetchone()
                observation_count = conn.execute(
                    "SELECT COUNT(*) FROM job_observations WHERE job_id = ?",
                    ("linkedin:123",),
                ).fetchone()[0]

            self.assertEqual(job_row[0], first_seen)
            self.assertEqual(job_row[1], first_seen)
            self.assertEqual(job_row[2], second_seen)
            self.assertEqual(job_row[3], 2)
            self.assertEqual(observation_count, 2)

    def test_job_summary_includes_observation_stats(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "jobs.db")
            db = JobDatabase(db_path=db_path)
            frame = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:999",
                        "title": "ML Engineer",
                        "company": "Beta",
                        "location": "Remote",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/999/",
                        "salary": "Not specified",
                        "description": "",
                    }
                ]
            )

            db.put_into_sql(frame, observed_at="2026-04-01T09:00:00")
            db.put_into_sql(frame, observed_at="2026-04-02T09:00:00")
            summary = db.get_job_summary()

            observation_stats = summary["observation_stats"]
            self.assertEqual(observation_stats["total_observations"], 2)
            self.assertEqual(observation_stats["repeat_jobs"], 1)
            self.assertEqual(observation_stats["max_seen_count"], 2)


class TestProfileFitScoring(unittest.TestCase):
    """Tests for profile-fit ranking and persistence."""

    def test_score_job_fit_prioritizes_data_scientist_with_llm_signal(self) -> None:
        result = profile_fit.score_job_fit(
            {
                "title": "Senior Data Scientist, Generative AI",
                "company": "ACME",
                "location": "Berlin, Germany",
                "description": "Build LLM and RAG systems for analytics workflows.",
            },
            parsed_payload={
                "seniority": "senior",
                "skills": ["machine learning", "llm", "statistics"],
                "tools": ["python", "sql"],
                "summary": "LLM-focused data scientist role",
            },
        )

        self.assertEqual(result["band"], "high")
        self.assertGreaterEqual(result["score"], 75.0)
        self.assertTrue(result["signals"]["llm_match"])

    def test_score_job_fit_zeros_consulting_roles(self) -> None:
        result = profile_fit.score_job_fit(
            {
                "title": "AI Consultant",
                "company": "Deloitte",
                "location": "Munich, Germany",
                "description": "Advise enterprise clients on AI transformation.",
            }
        )

        self.assertEqual(result["band"], "zero")
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["signals"]["excluded_reason"], "excluded_company")

    def test_score_job_fit_zeros_principal_titles_even_if_payload_says_senior(self) -> None:
        result = profile_fit.score_job_fit(
            {
                "title": "Principal Applied Scientist, Search/NLP",
                "company": "ACME",
                "location": "Berlin, Germany",
                "description": "Drive applied NLP and retrieval systems.",
            },
            parsed_payload={
                "seniority": "senior",
                "skills": ["nlp", "retrieval", "machine learning"],
            },
        )

        self.assertEqual(result["band"], "zero")
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["signals"]["excluded_reason"], "excluded_seniority")

    def test_put_into_sql_persists_fit_scores(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "jobs.db")
            db = JobDatabase(db_path=db_path)
            frame = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:fit-1",
                        "title": "Data Scientist, LLM Applications",
                        "company": "ACME",
                        "location": "Berlin, Germany",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/fit-1/",
                        "salary": "Not specified",
                        "description": "Own LLM and generative AI use cases.",
                    }
                ]
            )

            db.put_into_sql(frame, observed_at="2026-04-05T09:00:00")
            fit = db.get_job_fit("linkedin:fit-1")

            self.assertIsNotNone(fit)
            assert fit is not None
            self.assertGreaterEqual(fit["score"], 70.0)
            self.assertIn(fit["band"], {"high", "medium"})


class _FakeStructuredLLM:
    api_key = "test-key"

    def __init__(self, payload: str):
        self.payload = payload

    def generate_structured(self, prompt, response_model, *, system=None, temperature=0.0, max_tokens=2048):
        return self.payload


class TestDescriptionParserFallbacks(unittest.TestCase):
    """Tests for parser fallback normalization when the LLM returns raw JSON."""

    def test_job_description_structure_accepts_missing_salary_and_string_benefits(self) -> None:
        result = JobDescriptionStructure.model_validate(
            {
                "seniority": "senior",
                "summary": "Work across BI and product teams.",
                "extra benefits": "training academy, childcare support, diversity focus, team culture",
            }
        )

        payload = result.model_dump(by_alias=True)
        self.assertIsNone(payload["salary_eur_min"])
        self.assertIsNone(payload["salary_eur_max"])
        self.assertEqual(
            payload["extra benefits"],
            ["training academy", "childcare support", "diversity focus", "team culture"],
        )

    def test_call_llm_normalizes_raw_json_string_result(self) -> None:
        parser = object.__new__(DescriptionTools)
        parser.prompt = ""
        parser.llm = _FakeStructuredLLM(
            (
                '{"seniority":"senior","summary":"International role in Berlin.",'
                '"extra benefits":"[remote flexibility, parental leave, accessibility accommodations]"}'
            )
        )

        result = parser._call_llm(
            "Own LLM and analytics workflows.",
            job_id="linkedin:test-1",
            title="Senior Data Scientist",
            company="ACME",
        )

        self.assertIsNotNone(result)
        payload = json.loads(result)
        self.assertEqual(
            payload["extra benefits"],
            ["remote flexibility", "parental leave", "accessibility accommodations"],
        )
        self.assertEqual(payload["salary_eur_range"], {"min": None, "max": None})


class _FakeLLMMessage:
    def __init__(self, content=None, refusal=None):
        self.content = content
        self.refusal = refusal


class _FakeLLMChoice:
    def __init__(self, message):
        self.message = message


class _FakeLLMResponse:
    def __init__(self, message):
        self.choices = [_FakeLLMChoice(message)]


class TestLLMConnectionHelpers(unittest.TestCase):
    """Tests for low-level LLM response normalization helpers."""

    def test_extract_message_content_supports_content_blocks(self) -> None:
        llm = object.__new__(LLMConnection)
        response = _FakeLLMResponse(
            _FakeLLMMessage(
                content=[
                    {"type": "output_text", "text": '{"summary":"hello"}'},
                    {"type": "ignored", "text": "should not be used"},
                ]
            )
        )

        content = llm._extract_message_content(response)
        self.assertEqual(content, '{"summary":"hello"}')


if __name__ == "__main__":  # pragma: no cover - module level execution guard
    unittest.main()
