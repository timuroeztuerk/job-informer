"""Comprehensive tests for backend utility helpers.

These tests previously lived across multiple modules. They are now gathered
into a single test module while still exercising the same behaviours that were
covered before.
"""

from __future__ import annotations

import json
import os
import unittest
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pandas as pd

from backend.src.agents.ai_purger import AIPurger, AIPurger_JSON_CLASS
from backend.src.agents.parser import DescriptionTools, JobDescriptionStructure
from backend.src.utils import data_utils, filtering, profile_fit
from backend.src.utils.database import JobDatabase
from backend.src.utils.db_summary import build_db_summary
from backend.src.utils.openai_responses_client import (
    LLMCallTelemetry,
    LLMUsage,
    OpenAIResponsesClient,
    StructuredLLMResult,
)


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

    def test_academic_title_filter_handles_english_and_german_roles(self) -> None:
        academic_titles = [
            "PostDoc in Forest Ecology",
            "Professorin für Datenbanken",
            "Wissenschaftliche*n Mitarbeiter*in am Lehrstuhl Datenbanken",
            "Wissenschaftlicher Mitarbeiter: Robotische Handhabungstechnik",
        ]
        for title in academic_titles:
            self.assertTrue(filtering.should_filter_academic_title(title), title)

        self.assertFalse(filtering.should_filter_academic_title("AI Researcher — Training Optimization"))
        self.assertFalse(filtering.should_filter_academic_title("Research Scientist at Pharma Company"))

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

            first_seen = "2026-04-01T08:00:00+00:00"
            second_seen = "2026-04-03T08:00:00+00:00"
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

    def test_archive_and_restore_job_records_filter_audit(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "jobs.db")
            db = JobDatabase(db_path=db_path)
            frame = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:archive-1",
                        "title": "Working Student Data Science",
                        "company": "ACME",
                        "location": "Berlin",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/archive-1/",
                        "salary": "Not specified",
                        "description": "",
                    }
                ]
            )

            db.put_into_sql(frame, observed_at="2026-04-01T09:00:00")
            archive_summary = db.archive_jobs_with_filter_decisions(
                [
                    {
                        "job_id": "linkedin:archive-1",
                        "decision_source": "rule",
                        "decision_action": "archive",
                        "filter_name": "study_title",
                        "matched_value": r"\bworking student\b",
                        "reason": "Archived as internship or study-track role based on title",
                    }
                ]
            )

            self.assertEqual(archive_summary["archived"], 1)
            with db._get_connection() as conn:  # noqa: SLF001
                row = conn.execute(
                    "SELECT archived_at, archived_reason FROM jobs WHERE job_id = ?",
                    ("linkedin:archive-1",),
                ).fetchone()

            self.assertIsNotNone(row[0])
            self.assertEqual(row[1], "Archived as internship or study-track role based on title")
            self.assertEqual(len(db.get_filter_decisions("linkedin:archive-1")), 1)

            restored = db.restore_job("linkedin:archive-1")
            self.assertTrue(restored)

            with db._get_connection() as conn:  # noqa: SLF001
                restored_row = conn.execute(
                    "SELECT archived_at, archived_reason, analyzed FROM jobs WHERE job_id = ?",
                    ("linkedin:archive-1",),
                ).fetchone()

            self.assertIsNone(restored_row[0])
            self.assertIsNone(restored_row[1])
            self.assertEqual(restored_row[2], 0)
            self.assertEqual(len(db.get_filter_decisions("linkedin:archive-1")), 2)

    def test_archived_jobs_are_excluded_from_active_summary(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "jobs.db")
            db = JobDatabase(db_path=db_path)
            frame = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:active-1",
                        "title": "Data Scientist",
                        "company": "ACME",
                        "location": "Berlin",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/active-1/",
                        "salary": "Not specified",
                        "description": "",
                    },
                    {
                        "job_id": "linkedin:archived-1",
                        "title": "Working Student Data Science",
                        "company": "Beta",
                        "location": "Munich",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/archived-1/",
                        "salary": "Not specified",
                        "description": "",
                    },
                ]
            )

            db.put_into_sql(frame, observed_at="2026-04-01T09:00:00")
            db.archive_jobs(["linkedin:archived-1"], archived_reason="Archived for test")

            summary = db.get_job_summary()

            self.assertEqual(summary["total_jobs"], 1)
            self.assertEqual(summary["archived_jobs"], 1)
            self.assertEqual(summary["jobs_by_source"], {"LinkedIn": 1})


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

    def test_put_into_sql_does_not_recompute_deferred_fit_scores(self) -> None:
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
            self.assertIsNone(db.get_job_fit("linkedin:fit-1"))

            db.recompute_fit_scores(job_ids=["linkedin:fit-1"])
            fit = db.get_job_fit("linkedin:fit-1")
            self.assertIsNotNone(fit)
            assert fit is not None
            self.assertGreaterEqual(fit["score"], 70.0)
            self.assertIn(fit["band"], {"high", "medium"})


def _make_test_config(**overrides):
    defaults = {
        "openai_api_key": "test-key",
        "openai_model": "gpt-5-mini",
        "desc_parser_model": "gpt-5-mini",
        "desc_parser_prompt": "",
        "desc_parser_version": 1,
        "desc_parser_min_chars": 20,
        "desc_parser_max_chars": 12000,
        "desc_parser_dry_run": False,
        "desc_parser_concurrency": 4,
        "desc_parser_batch_size": 25,
        "desc_parser_max_batches": 10,
        "llm_timeout_seconds": 1.0,
        "llm_max_retries": 1,
        "llm_retry_base_delay": 0.0,
        "llm_retry_max_delay": 0.0,
        "llm_retry_jitter": 0.0,
        "ai_purge_min_confidence": 0.75,
        "ai_purge_max_ratio": 0.35,
        "ai_purge_max_jobs": 0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _telemetry(
    *,
    status: str = "success",
    model: str = "gpt-5-mini",
    retry_count: int = 0,
    exhausted_retries: bool = False,
    refusal_text: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    output_preview: str | None = '{"summary":"preview"}',
    response_id: str | None = "resp_test",
    input_tokens: int | None = 10,
    output_tokens: int | None = 20,
) -> LLMCallTelemetry:
    total_tokens = None
    if input_tokens is not None or output_tokens is not None:
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return LLMCallTelemetry(
        model=model,
        status=status,
        started_at="2026-04-04T10:00:00+00:00",
        finished_at="2026-04-04T10:00:01+00:00",
        latency_ms=125.0,
        response_id=response_id,
        retry_count=retry_count,
        exhausted_retries=exhausted_retries,
        refusal_text=refusal_text,
        error_type=error_type,
        error_message=error_message,
        output_preview=output_preview,
        usage=LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        ),
    )


class _SequenceStructuredClient:
    api_key = "test-key"
    model = "gpt-5-mini"

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def _next_result(self):
        if not self.results:
            raise AssertionError("No fake LLM results left")
        return self.results.pop(0)

    async def parse_structured_async(
        self,
        *,
        response_model,
        input,
        instructions=None,
        temperature=None,
    ):
        self.calls.append(
            {
                "mode": "async",
                "response_model": response_model,
                "input": input,
                "instructions": instructions,
                "temperature": temperature,
            }
        )
        return self._next_result()

    def parse_structured(
        self,
        *,
        response_model,
        input,
        instructions=None,
        temperature=None,
    ):
        self.calls.append(
            {
                "mode": "sync",
                "response_model": response_model,
                "input": input,
                "instructions": instructions,
                "temperature": temperature,
            }
        )
        return self._next_result()

    def get_stats(self):
        return {"requests": len(self.calls), "model": self.model}


class _FakeResponsesAPI:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.requests = []

    async def parse(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        if not self.outcomes:
            raise AssertionError("No fake Responses API outcomes left")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeResponsesClient:
    def __init__(self, outcomes):
        self.responses = _FakeResponsesAPI(outcomes)


class TestDescriptionParserResponses(unittest.TestCase):
    """Tests for structured parser normalization and persistence."""

    def test_job_description_structure_accepts_missing_salary_and_string_benefits(self) -> None:
        result = JobDescriptionStructure.model_validate(
            {
                "seniority": "senior",
                "summary": "Work across BI and product teams.",
                "extra benefits": "training academy, childcare support, diversity focus, team culture",
            }
        )

        payload = result.model_dump(by_alias=True)
        self.assertEqual(payload["salary_eur_range"], {"min": None, "max": None})
        self.assertEqual(
            payload["extra benefits"],
            ["training academy", "childcare support", "diversity focus", "team culture"],
        )

    def test_parse_batch_persists_parse_status_and_telemetry_on_success(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=os.path.join(tmp_dir, "jobs.db"))
            parser = DescriptionTools(
                config=_make_test_config(desc_parser_min_chars=10),
                db=db,
                llm_client=_SequenceStructuredClient(
                    [
                        StructuredLLMResult(
                            parsed=JobDescriptionStructure.model_validate(
                                {
                                    "seniority": "senior",
                                    "skills": ["machine learning", "llm"],
                                    "summary": "LLM-focused data scientist role.",
                                }
                            ),
                            telemetry=_telemetry(status="success"),
                        )
                    ]
                ),
            )
            jobs_df = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:parse-1",
                        "title": "Senior Data Scientist",
                        "company": "ACME",
                        "description": "Build and deploy LLM systems for enterprise analytics teams.",
                    }
                ]
            )

            stored = parser.parse_batch(jobs_df)

            self.assertEqual(stored, 1)
            status = db.get_parse_status("linkedin:parse-1")
            self.assertIsNotNone(status)
            assert status is not None
            self.assertEqual(status["status"], "success")
            self.assertEqual(status["attempt_count"], 1)

            with db._get_connection() as conn:  # noqa: SLF001
                parsed_rows = conn.execute("SELECT COUNT(*) FROM parsed_descriptions").fetchone()[0]
                attempt_rows = conn.execute(
                    "SELECT COUNT(*) FROM llm_attempts WHERE task_type = 'parser'"
                ).fetchone()[0]

            self.assertEqual(parsed_rows, 1)
            self.assertEqual(attempt_rows, 1)

    def test_parse_batch_failed_attempts_increment_state(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=os.path.join(tmp_dir, "jobs.db"))
            client = _SequenceStructuredClient(
                [
                    StructuredLLMResult(
                        parsed=None,
                        telemetry=_telemetry(
                            status="failed",
                            error_type="ValueError",
                            error_message="No parsed structured output returned from LLM.",
                        ),
                    ),
                    StructuredLLMResult(
                        parsed=None,
                        telemetry=_telemetry(
                            status="failed",
                            error_type="ValueError",
                            error_message="No parsed structured output returned from LLM.",
                            retry_count=1,
                            exhausted_retries=True,
                        ),
                    ),
                ]
            )
            parser = DescriptionTools(config=_make_test_config(desc_parser_min_chars=10), db=db, llm_client=client)
            jobs_df = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:parse-fail",
                        "title": "Data Scientist",
                        "company": "ACME",
                        "description": "This description is long enough to be parsed repeatedly by the test harness.",
                    }
                ]
            )

            self.assertEqual(parser.parse_batch(jobs_df), 0)
            self.assertEqual(parser.parse_batch(jobs_df), 0)

            status = db.get_parse_status("linkedin:parse-fail")
            self.assertIsNotNone(status)
            assert status is not None
            self.assertEqual(status["status"], "failed")
            self.assertEqual(status["attempt_count"], 2)
            self.assertEqual(status["last_error_type"], "ValueError")

            with db._get_connection() as conn:  # noqa: SLF001
                attempt_rows = conn.execute(
                    "SELECT COUNT(*) FROM llm_attempts WHERE task_type = 'parser' AND entity_id = ?",
                    ("linkedin:parse-fail",),
                ).fetchone()[0]
            self.assertEqual(attempt_rows, 2)

    def test_successful_retry_clears_previous_error_state(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=os.path.join(tmp_dir, "jobs.db"))
            client = _SequenceStructuredClient(
                [
                    StructuredLLMResult(
                        parsed=None,
                        telemetry=_telemetry(
                            status="failed",
                            error_type="ValueError",
                            error_message="Temporary parse failure.",
                        ),
                    ),
                    StructuredLLMResult(
                        parsed=JobDescriptionStructure.model_validate(
                            {
                                "seniority": "mid",
                                "tools": ["python", "sql"],
                                "summary": "Recovered parse on retry.",
                            }
                        ),
                        telemetry=_telemetry(status="success", retry_count=1),
                    ),
                ]
            )
            parser = DescriptionTools(config=_make_test_config(desc_parser_min_chars=10), db=db, llm_client=client)
            jobs_df = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:parse-retry",
                        "title": "Data Scientist",
                        "company": "ACME",
                        "description": "A long enough description to test failure followed by success on retry handling.",
                    }
                ]
            )

            parser.parse_batch(jobs_df)
            parser.parse_batch(jobs_df)

            status = db.get_parse_status("linkedin:parse-retry")
            self.assertIsNotNone(status)
            assert status is not None
            self.assertEqual(status["status"], "success")
            self.assertEqual(status["attempt_count"], 2)
            self.assertIsNone(status["last_error_type"])
            self.assertIsNone(status["last_error_message"])
            self.assertIsNotNone(status["last_success_at"])

    def test_build_db_summary_includes_parser_telemetry(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=os.path.join(tmp_dir, "jobs.db"))
            parser = DescriptionTools(
                config=_make_test_config(desc_parser_min_chars=10),
                db=db,
                llm_client=_SequenceStructuredClient(
                    [
                        StructuredLLMResult(
                            parsed=JobDescriptionStructure.model_validate({"summary": "One success."}),
                            telemetry=_telemetry(status="success", input_tokens=11, output_tokens=9),
                        ),
                        StructuredLLMResult(
                            parsed=None,
                            telemetry=_telemetry(
                                status="failed",
                                error_type="RuntimeError",
                                error_message="Parser exploded.",
                                input_tokens=7,
                                output_tokens=0,
                            ),
                        ),
                    ]
                ),
            )
            jobs_df = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:sum-1",
                        "title": "Role 1",
                        "company": "ACME",
                        "description": "This description is long enough for summary telemetry success path.",
                    },
                    {
                        "job_id": "linkedin:sum-2",
                        "title": "Role 2",
                        "company": "ACME",
                        "description": "This second description is also long enough for the parser failure path.",
                    },
                ]
            )

            parser.parse_batch(jobs_df)
            summary = build_db_summary(db)

            self.assertIn("parser_telemetry", summary)
            self.assertEqual(summary["parser_telemetry"]["attempts"], 2)
            self.assertEqual(summary["parser_telemetry"]["success_count"], 1)
            self.assertEqual(summary["parser_telemetry"]["failure_count"], 1)


class TestAIPurgerResponses(unittest.TestCase):
    """Tests for purge decisions and telemetry via the shared Responses client."""

    def test_engineering_titles_are_always_purge_candidates(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=os.path.join(tmp_dir, "jobs.db"))
            db.put_into_sql(
                pd.DataFrame(
                    [
                        {
                            "job_id": "linkedin:purge-engineer",
                            "title": "AI Engineer",
                            "company": "ACME AI",
                            "location": "Berlin",
                            "source": "LinkedIn",
                            "url": "https://www.linkedin.com/jobs/view/purge-engineer/",
                            "salary": "Not specified",
                            "description": "",
                        }
                    ]
                ),
                observed_at="2026-04-04T09:00:00",
            )
            client = _SequenceStructuredClient([])
            purger = AIPurger(
                config=_make_test_config(),
                db=db,
                llm_client=client,
                process_all=True,
            )

            result = purger.run_purge_mode()

            self.assertTrue(result["success"])
            self.assertEqual(result["jobs_to_purge"], 1)
            self.assertEqual(result["jobs_archived"], 1)
            self.assertEqual(client.calls, [])
            with db._get_connection() as conn:  # noqa: SLF001
                row = conn.execute(
                    "SELECT archived_at, archived_reason FROM jobs WHERE job_id = ?",
                    ("linkedin:purge-engineer",),
                ).fetchone()
            self.assertIsNotNone(row[0])
            self.assertIn("engineering role", row[1])

    def test_prepare_records_purge_llm_attempts(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=os.path.join(tmp_dir, "jobs.db"))
            db.put_into_sql(
                pd.DataFrame(
                    [
                        {
                            "job_id": "linkedin:purge-1",
                            "title": "Retail Store Associate",
                            "company": "ACME Retail",
                            "location": "Berlin",
                            "source": "LinkedIn",
                            "url": "https://www.linkedin.com/jobs/view/purge-1/",
                            "salary": "Not specified",
                            "description": "",
                        }
                    ]
                ),
                observed_at="2026-04-04T09:00:00",
            )
            client = _SequenceStructuredClient(
                [
                    StructuredLLMResult(
                        parsed=AIPurger_JSON_CLASS(
                            purge_candidates=[
                                {
                                    "id": "1",
                                    "reason": "Clear irrelevant non-technical retail role",
                                    "confidence": 0.95,
                                    "purge": True,
                                }
                            ]
                        ),
                        telemetry=_telemetry(status="success"),
                    )
                ]
            )
            purger = AIPurger(
                config=_make_test_config(),
                db=db,
                llm_client=client,
                process_all=True,
            )

            result = purger.prepare()

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["job_ids_to_purge"], ["linkedin:purge-1"])
            with db._get_connection() as conn:  # noqa: SLF001
                row = conn.execute(
                    "SELECT task_type, status FROM llm_attempts WHERE task_type = 'purge'"
                ).fetchone()
            self.assertEqual(row[0], "purge")
            self.assertEqual(row[1], "success")


class TestOpenAIResponsesClient(unittest.TestCase):
    """Tests for the shared Responses API structured client."""

    def test_parse_structured_success_extracts_usage_and_preview(self) -> None:
        client = OpenAIResponsesClient(
            api_key="test-key",
            model="gpt-5-mini",
            timeout_seconds=1,
            max_retries=0,
            retry_base_delay=0,
            retry_max_delay=0,
            retry_jitter=0,
        )
        client._client = _FakeResponsesClient(  # noqa: SLF001
            [
                SimpleNamespace(
                    id="resp_success",
                    output_parsed=JobDescriptionStructure.model_validate({"summary": "hello"}),
                    output=[
                        SimpleNamespace(
                            content=[SimpleNamespace(type="output_text", text='{"summary":"hello"}')]
                        )
                    ],
                    usage=SimpleNamespace(input_tokens=12, output_tokens=8, total_tokens=20),
                )
            ]
        )

        result = client.parse_structured(
            response_model=JobDescriptionStructure,
            input="DESCRIPTION: hello",
        )

        self.assertIsNotNone(result.parsed)
        self.assertEqual(result.telemetry.status, "success")
        self.assertEqual(result.telemetry.usage.total_tokens, 20)
        self.assertEqual(result.telemetry.output_preview, '{"summary":"hello"}')
        self.assertNotIn("max_output_tokens", client._client.responses.requests[0])  # noqa: SLF001

    def test_parse_structured_handles_refusal(self) -> None:
        client = OpenAIResponsesClient(
            api_key="test-key",
            model="gpt-5-mini",
            timeout_seconds=1,
            max_retries=0,
            retry_base_delay=0,
            retry_max_delay=0,
            retry_jitter=0,
        )
        client._client = _FakeResponsesClient(  # noqa: SLF001
            [
                SimpleNamespace(
                    id="resp_refusal",
                    output_parsed=None,
                    output=[SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="cannot comply")])],
                    usage=SimpleNamespace(input_tokens=3, output_tokens=0, total_tokens=3),
                )
            ]
        )

        result = client.parse_structured(
            response_model=JobDescriptionStructure,
            input="DESCRIPTION: refuse",
        )

        self.assertIsNone(result.parsed)
        self.assertEqual(result.telemetry.status, "refusal")
        self.assertEqual(result.telemetry.refusal_text, "cannot comply")

    def test_parse_structured_uses_valid_json_when_sdk_omits_output_parsed(self) -> None:
        client = OpenAIResponsesClient(
            api_key="test-key",
            model="gpt-5-mini",
            timeout_seconds=1,
            max_retries=1,
            retry_base_delay=0,
            retry_max_delay=0,
            retry_jitter=0,
        )
        client._client = _FakeResponsesClient(  # noqa: SLF001
            [
                SimpleNamespace(
                    id="resp_json_fallback",
                    output_parsed=None,
                    output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text='{"summary":"from JSON"}')])],
                    usage=SimpleNamespace(input_tokens=5, output_tokens=4, total_tokens=9),
                ),
            ]
        )

        result = client.parse_structured(
            response_model=JobDescriptionStructure,
            input="DESCRIPTION: JSON fallback",
        )

        self.assertIsNotNone(result.parsed)
        self.assertEqual(result.telemetry.status, "success")
        self.assertEqual(result.parsed.summary, "from JSON")
        self.assertEqual(client._client.responses.calls, 1)  # noqa: SLF001

    def test_parse_structured_does_not_retry_completed_response_without_output(self) -> None:
        client = OpenAIResponsesClient(
            api_key="test-key",
            model="gpt-5-mini",
            timeout_seconds=1,
            max_retries=3,
            retry_base_delay=0,
            retry_max_delay=0,
            retry_jitter=0,
        )
        client._client = _FakeResponsesClient(  # noqa: SLF001
            [
                SimpleNamespace(
                    id="resp_incomplete",
                    status="incomplete",
                    incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                    output=[],
                    output_parsed=None,
                    usage=SimpleNamespace(input_tokens=5, output_tokens=4, total_tokens=9),
                )
            ]
        )

        result = client.parse_structured(
            response_model=JobDescriptionStructure,
            input="DESCRIPTION: incomplete",
        )

        self.assertIsNone(result.parsed)
        self.assertEqual(result.telemetry.status, "failed")
        self.assertEqual(result.telemetry.response_id, "resp_incomplete")
        self.assertEqual(result.telemetry.usage.total_tokens, 9)
        self.assertIn("status=incomplete", result.telemetry.error_message or "")
        self.assertIn("incomplete_reason=max_output_tokens", result.telemetry.error_message or "")
        self.assertEqual(client._client.responses.calls, 1)  # noqa: SLF001

    def test_parse_structured_returns_failed_for_non_retryable_error(self) -> None:
        client = OpenAIResponsesClient(
            api_key="test-key",
            model="gpt-5-mini",
            timeout_seconds=1,
            max_retries=2,
            retry_base_delay=0,
            retry_max_delay=0,
            retry_jitter=0,
        )
        client._client = _FakeResponsesClient([RuntimeError("boom")])  # noqa: SLF001

        result = client.parse_structured(
            response_model=JobDescriptionStructure,
            input="DESCRIPTION: fail",
        )

        self.assertIsNone(result.parsed)
        self.assertEqual(result.telemetry.status, "failed")
        self.assertEqual(result.telemetry.error_type, "RuntimeError")
        self.assertEqual(client._client.responses.calls, 1)  # noqa: SLF001


class TestParserApiSurface(unittest.TestCase):
    """Tests for compact parser status exposure on the job API."""

    def test_get_job_includes_parse_status(self) -> None:
        import importlib

        api_module = importlib.import_module("backend.api")

        with TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "jobs.db")
            db = JobDatabase(db_path=db_path)
            db.put_into_sql(
                pd.DataFrame(
                    [
                        {
                            "job_id": "linkedin:api-1",
                            "title": "Data Scientist",
                            "company": "ACME",
                            "location": "Berlin",
                            "source": "LinkedIn",
                            "url": "https://www.linkedin.com/jobs/view/api-1/",
                            "salary": "Not specified",
                            "description": "Long enough description for parse status exposure in the API response.",
                        }
                    ]
                ),
                observed_at="2026-04-04T09:00:00",
            )
            db.upsert_parse_job_state(
                job_id="linkedin:api-1",
                desc_hash="hash-api-1",
                version=1,
                model="gpt-5-mini",
                status="failed",
                last_attempt_at="2026-04-04T10:00:00",
                last_error_type="RuntimeError",
                last_error_message="Example parser failure",
            )

            previous_settings = api_module.APP_SETTINGS
            previous_job_db = api_module.job_db
            try:
                api_module.APP_SETTINGS = api_module.AppSettings(
                    db_path=db_path,
                    run_store_db_path=db_path,
                    api_token="",
                    max_log_bytes=previous_settings.max_log_bytes,
                    frontend_dist_dir=previous_settings.frontend_dist_dir,
                )
                api_module.job_db = api_module.JobDatabase(db_path)

                job = api_module.get_job(
                    "linkedin:api-1",
                    fit_profile_id=api_module.DEFAULT_FIT_PROFILE_ID,
                    _=True,
                )
            finally:
                api_module.APP_SETTINGS = previous_settings
                api_module.job_db = previous_job_db

            self.assertIn("parse_status", job)
            self.assertEqual(job["parse_status"]["status"], "failed")
            self.assertEqual(job["parse_status"]["last_error_type"], "RuntimeError")


if __name__ == "__main__":  # pragma: no cover - module level execution guard
    unittest.main()
