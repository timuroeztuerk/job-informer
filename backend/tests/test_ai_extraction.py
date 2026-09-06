"""Output, source continuity, job selection, and async queue behavior; no paid evals."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
import openai
import pandas as pd
from pydantic import ValidationError

from backend import api
from backend.src.ai import store as store_module
from backend.src.ai.models import EducationRequirement, ExperienceYears, JobExtraction, Requirement, validate_evidence
from backend.src.ai.prompt import contract, encode
from backend.src.ai.service import AIService
from backend.src.ai.store import AIStore
from backend.src.utils.database import JobDatabase


COPY = "Du entwickelst mit Python zuverlässige Datenprodukte und arbeitest mit unserem Team an Analysen.\nWe offer flexible working hours and a collaborative environment."


def fixture():
    return {"description_languages": [
        {"code": "de", "evidence": [{"source_ref": "description.1", "quote": "Du entwickelst mit Python"}]},
        {"code": "en", "evidence": [{"source_ref": "description.2", "quote": "We offer flexible working hours"}]},
    ], "states": {"requirements": "stated", "education": "not_stated", "languages": "not_stated", "experience": "not_stated",
                   "work_arrangement": "not_stated", "responsibilities": "not_stated", "seniority": "stated", "employment_type": "stated"},
        "requirements": [{"term": "Python", "category": "programming_language", "proficiency": None,
                          "strength": "mentioned", "condition": None, "alternative_group": None, "alternative_option": None,
                          "evidence": [{"source_ref": "description.1", "quote": "Du entwickelst mit Python"}]}],
        "education": [], "languages": [], "experience": [], "work_arrangement": [], "responsibilities": [],
        "seniority": [{"value": "Mid-Senior level", "evidence": [{"source_ref": "criteria.0", "quote": "Mid-Senior level"}]}],
        "employment_type": [{"value": "Full-time", "evidence": [{"source_ref": "criteria.1", "quote": "Full-time"}]}],
        "conflicts": []}


def seed(path, count=12):
    db = JobDatabase(path)
    db.put_into_sql(pd.DataFrame([{"job_id": f"linkedin:{1000+i}", "title": "Data Scientist", "company": f"Company {i}",
                                  "source": "LinkedIn", "location": "Berlin"} for i in range(count)]))
    with db._get_connection() as conn:
        for i in range(count):
            source_id = conn.execute("INSERT INTO description_sources(job_id,source_url,fetched_at,content_sha256,body,content_type) VALUES (?,?,?,'source',?,'text/html')",
                                     (f"linkedin:{1000+i}", f"https://www.linkedin.com/jobs/view/{1000+i}/", "2026-09-05T10:00:00Z", COPY.encode())).lastrowid
            data = {"title": "Data Scientist", "description_text": COPY,
                    "criteria": [{"key": key, "label": label, "value": value, "evidence": {"text": value}} for key, label, value in [
                        ("seniority", "Seniority level", "Mid-Senior level"), ("employment_type", "Employment type", "Full-time"),
                        ("job_function", "Job function", "Finance and Sales"), ("industries", "Industries", "Financial Services")]]}
            conn.execute("INSERT INTO description_extractions(source_id,extractor,extractor_version,schema_version,extracted_at,content_sha256,data_json) VALUES (?,'linkedin_public_job','1','1','2026-09-05T10:00:00Z','text',?)", (source_id, encode(data)))
    return db


def response(payload=None, status="completed"):
    return SimpleNamespace(id="resp_test", _request_id="req_test", model="gpt-5.4-mini-2026-03-17", service_tier="flex",
                           status=status, usage=SimpleNamespace(input_tokens=100, output_tokens=200,
                           input_tokens_details=SimpleNamespace(cached_tokens=20), output_tokens_details=SimpleNamespace(reasoning_tokens=80)),
                           output_text=encode(payload or fixture()))


class TestExtractionContract(unittest.TestCase):
    def test_experience_bounds_keep_thresholds_and_target_ranges_distinct(self):
        for kind, lower, upper in [("minimum", 5, None), ("maximum", None, 3),
                                   ("target_range", 2, 4), ("ambiguous_minimum", 1, 2), ("exact", 3, 3)]:
            with self.subTest(kind=kind):
                self.assertEqual(ExperienceYears(kind=kind, lower=lower, upper=upper).kind, kind)
        for kind, lower, upper in [("minimum", 1, 2), ("maximum", 1, 2),
                                   ("target_range", 4, 2), ("ambiguous_minimum", 1, None), ("exact", 1, 2)]:
            with self.subTest(invalid=kind), self.assertRaises(ValidationError):
                ExperienceYears(kind=kind, lower=lower, upper=upper)

    def test_education_and_compound_options_preserve_conditions_and_strength(self):
        context = {"strength": "required", "condition": "for India", "alternative_group": None,
                   "alternative_option": None, "evidence": [{"source_ref": "description.1", "quote": "Bachelor's degree"}]}
        education = EducationRequirement(**context, qualification="Bachelor's degree", level="bachelor", fields_of_study=["Computer Science"])
        payload = fixture()
        payload["education"] = [education.model_dump()]
        payload["states"]["education"] = "stated"
        parsed = JobExtraction.model_validate(payload)
        self.assertEqual(parsed.education[0].condition, "for India")
        self.assertEqual(parsed.education[0].strength, "required")
        with self.assertRaises(ValidationError):
            EducationRequirement(**{**context, "alternative_group": "degree"}, qualification="Degree", level="degree_unspecified", fields_of_study=[])
        options = [Requirement(**{**context, "condition": None, "alternative_group": "tools", "alternative_option": option},
                               term=term, category="tool", proficiency=None)
                   for term, option in [("Palantir", "A"), ("MSS", "A"), ("Onebrief", "B")]]
        self.assertEqual(options[0].alternative_option, options[1].alternative_option)
        self.assertNotEqual(options[1].alternative_option, options[2].alternative_option)

    def test_atomic_names_normalize_without_changing_evidence_or_applicability(self):
        payload = fixture()
        payload["requirements"] = [{**payload["requirements"][0], "term": "sql", "category": "tool",
                                    "proficiency": "advanced", "condition": "for India",
                                    "evidence": [{"source_ref": "description.1", "quote": "advanced SQL"}]}]
        sources = {"description.1": "Du entwickelst mit Python; advanced SQL for India", "description.2": "We offer flexible working hours",
                   "criteria.0": "Mid-Senior level", "criteria.1": "Full-time"}
        item = validate_evidence(JobExtraction.model_validate(payload), sources)["requirements"][0]
        self.assertEqual((item["canonical_term"], item["category"]), ("SQL", "programming_language"))
        self.assertEqual((item["proficiency"], item["condition"]), ("advanced", "for India"))
        self.assertEqual(item["evidence"][0]["quote"], "advanced SQL")


class TestAIStore(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)/"jobs.db"
        self.db = seed(self.path)
        self.store = AIStore(self.path)

    def test_bulk_batches_advance_past_cached_queued_and_ineligible_jobs(self):
        path = self.path.with_name("batches.db")
        db = seed(path, count=205)
        store = AIStore(path)
        for job_id in ("linkedin:1000", "linkedin:1001"):
            store.enqueue(job_id)
        for work in store.claim(2):
            store.finish(work, payload=fixture())
        with store.connect() as conn:
            conn.execute("DELETE FROM description_extractions WHERE source_id IN (SELECT source_id FROM description_sources WHERE job_id='linkedin:1002')")
            conn.execute("UPDATE jobs SET is_favorite=1 WHERE job_id='linkedin:1204'")
        db.archive_jobs(["linkedin:1003"], archived_reason="Not a fit")
        self.assertEqual(store.enqueue(), 100)
        with store.connect() as conn:
            self.assertEqual(conn.execute("SELECT job_id FROM ai_work WHERE status='queued' ORDER BY work_id LIMIT 1").fetchone()[0], "linkedin:1204")
        self.assertEqual(store.enqueue(), 100)
        self.assertEqual(store.enqueue(), 1)
        self.assertEqual(store.enqueue(), 0)
        self.assertEqual(store.status()["counts"], {"succeeded": 2, "queued": 201})
        self.assertEqual(store.status()["batch_size"], 100)
        with store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_work WHERE job_id IN ('linkedin:1002','linkedin:1003')").fetchone()[0], 0)

    def test_retry_is_limited_to_100_failed_jobs_per_click(self):
        path = self.path.with_name("retries.db")
        seed(path, count=102)
        store = AIStore(path)
        store.enqueue()
        store.enqueue()
        completed = store.claim(1)[0]
        store.finish(completed, payload=fixture())
        with store.connect() as conn:
            conn.execute("UPDATE ai_work SET status='failed',attempts=5 WHERE status='queued'")
        self.assertEqual(store.retry_failed(), 100)
        self.assertEqual(store.status()["counts"], {"succeeded": 1, "queued": 100, "failed": 1})
        self.assertEqual(store.retry_failed(), 1)
        self.assertEqual(store.retry_failed(), 0)
        self.assertEqual(store.status()["counts"], {"succeeded": 1, "queued": 101})

    def test_bulk_queue_reuses_existing_results_and_ignores_legacy_pilot_state(self):
        for index in range(10):
            self.store.enqueue(f"linkedin:{1000+index}")
        for work in self.store.claim(100):
            self.store.finish(work, payload=fixture())
        with self.store.connect() as conn:
            conn.execute("ALTER TABLE ai_queue_state ADD COLUMN pilot_job_ids TEXT")
            conn.execute("UPDATE ai_queue_state SET pilot_job_ids=?", (encode([f"linkedin:{1000+i}" for i in range(10)]),))
        self.store = AIStore(self.path)
        self.assertEqual(self.store.status()["counts"], {"succeeded": 10})
        self.assertEqual(self.store.enqueue(), 2)
        self.store.recover()
        self.assertEqual(self.store.enqueue(), 0)
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_work").fetchone()[0], 12)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_attempts").fetchone()[0], 10)

    def test_single_job_beyond_the_first_ten_does_not_queue_other_jobs(self):
        self.assertEqual(self.store.enqueue("linkedin:1011"), 1)
        self.assertEqual(self.store.enqueue("linkedin:1011"), 0)
        self.assertEqual(self.store.status()["counts"], {"queued": 1})
        self.assertEqual(len(self.store.status()["jobs"]), 1)
        self.assertEqual(self.store.enqueue("linkedin:1000"), 1)
        self.assertEqual(self.store.status()["counts"], {"queued": 2})

    def test_bulk_and_explicit_extraction_reject_archived_favorites_and_filtered_jobs(self):
        self.db.archive_jobs_with_filter_decisions([{"job_id": "linkedin:1000", "decision_source": "manual",
            "decision_action": "archive", "filter_name": "manual_archive", "reason": "Not a fit"}])
        with self.store.connect() as conn:
            conn.execute("UPDATE jobs SET is_favorite=1 WHERE job_id='linkedin:1000'")
            # Excluded outcomes must also fail closed if an old row lacks its archive timestamp.
            conn.execute("UPDATE jobs SET relevance_outcome='excluded' WHERE job_id='linkedin:1001'")
        self.assertEqual(self.store.enqueue(), 10)
        self.assertIsNone(self.store.job_result("linkedin:1000")["status"])
        for job_id in ("linkedin:1000", "linkedin:1001"):
            with self.subTest(job_id=job_id), self.assertRaisesRegex(ValueError, "Only active jobs"):
                self.store.enqueue(job_id)
        for work in self.store.claim(100):
            self.store.finish(work, payload=fixture())
        self.assertEqual(self.db.get_manual_overrides()["linkedin:1000"], "archive")
        with self.store.connect() as conn:
            archived_at, outcome = conn.execute("SELECT archived_at,relevance_outcome FROM jobs WHERE job_id='linkedin:1000'").fetchone()
        self.assertIsNotNone(archived_at)
        self.assertEqual(outcome, "manual_archive")

    def test_retry_and_status_exclude_inactive_jobs_but_preserve_history_and_usage(self):
        self.store.enqueue()
        work = self.store.claim(4)
        for item in work[:3]:
            self.store.finish(item, error_kind="output", message="Rejected output")
        self.store.finish(work[3], payload=fixture(), metadata={"usage": {"output_tokens": 200}, "estimated_cost_usd": 0.01})
        self.db.archive_jobs([work[0]["job_id"], work[3]["job_id"]], archived_reason="Not a fit")
        with self.store.connect() as conn:
            conn.execute("UPDATE jobs SET is_favorite=1 WHERE job_id=?", (work[0]["job_id"],))
            conn.execute("UPDATE jobs SET relevance_outcome='unrelated' WHERE job_id=?", (work[1]["job_id"],))
        status = self.store.status()
        self.assertEqual(status["counts"], {"failed": 1, "queued": 8})
        self.assertEqual(status["usage"]["output_tokens"], 200)
        self.assertEqual(status["estimated_cost_usd"], 0.01)
        self.assertTrue({item["job_id"] for item in work[:2]+work[3:4]}.isdisjoint(job["job_id"] for job in status["jobs"]))
        self.assertIsNotNone(self.store.job_result(work[3]["job_id"])["saved"])
        self.assertEqual(self.store.retry_failed(), 1)
        self.assertEqual(self.store.retry_failed(), 0)
        self.assertEqual(self.store.job_result(work[0]["job_id"])["status"], "failed")
        self.db.restore_job(work[0]["job_id"])
        self.assertEqual(self.store.enqueue(work[0]["job_id"]), 1)

    def test_queued_and_backoff_work_archived_later_is_skipped_on_recovery_resume_and_claim(self):
        for index, boundary in enumerate(("recover", "resume", "claim")):
            with self.subTest(boundary=boundary):
                job_id = f"linkedin:{1000+index}"
                self.store.enqueue(job_id)
                with self.store.connect() as conn:
                    conn.execute("UPDATE ai_work SET status='retry_wait',next_attempt_at=9999999999 WHERE job_id=?", (job_id,))
                self.db.archive_jobs([job_id], archived_reason="Filtered after queueing")
                if boundary == "claim":
                    self.assertEqual(self.store.claim(100), [])
                else:
                    getattr(self.store, boundary)()
                with self.store.connect() as conn:
                    state = conn.execute("SELECT status,error_kind,next_attempt_at FROM ai_work WHERE job_id=?", (job_id,)).fetchone()
                self.assertEqual(tuple(state), ("interrupted", "inactive", 0))
                self.assertEqual(self.store.status()["counts"], {})
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_attempts").fetchone()[0], 0)
        # The same gate catches ordinary queued work without spending an attempt.
        self.store.enqueue("linkedin:1011")
        self.db.archive_jobs(["linkedin:1011"], archived_reason="Archived after queueing")
        self.assertEqual(self.store.claim(100), [])
        self.assertEqual(self.store.job_result("linkedin:1011")["status"], "interrupted")

    def test_consolidated_active_role_remains_eligible_and_archived_group_is_blocked(self):
        self.db.archive_jobs(["linkedin:1000"], archived_reason="Old posting")
        with self.store.connect() as conn:
            conn.execute("INSERT INTO job_consolidations VALUES ('linkedin:1011', 'linkedin:1000', 'description')")
        # Canonical identity can point at an old posting; the review role is active.
        self.assertEqual(self.store.enqueue("linkedin:1011"), 1)
        self.assertEqual(self.store.claim(1)[0]["job_id"], "linkedin:1000")
        self.db.archive_jobs(["linkedin:1011"], archived_reason="Whole role archived")
        for job_id in ("linkedin:1000", "linkedin:1011"):
            with self.assertRaisesRegex(ValueError, "Only active jobs"):
                self.store.enqueue(job_id)
        self.assertEqual(self.store.status()["counts"], {})

    def test_single_job_retry_leaves_other_failures_and_completed_results_alone(self):
        self.store.enqueue("linkedin:1000")
        self.store.enqueue("linkedin:1001")
        for work in self.store.claim(2):
            self.store.finish(work, error_kind="output", message="Try again")
        self.assertEqual(self.store.enqueue("linkedin:1000"), 1)
        self.assertEqual(self.store.job_result("linkedin:1001")["status"], "failed")
        work = self.store.claim(1)[0]
        self.store.finish(work, payload=fixture())
        result = self.store.job_result("linkedin:1000")["saved"]
        self.assertEqual(self.store.enqueue("linkedin:1000"), 0)
        self.assertEqual(self.store.job_result("linkedin:1000")["saved"], result)
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_attempts").fetchone()[0], 3)

    def test_single_job_validates_saved_source_and_resolves_consolidated_members(self):
        with self.assertRaises(LookupError):
            self.store.enqueue("missing")
        with self.store.connect() as conn:
            conn.execute("DELETE FROM description_extractions WHERE source_id IN (SELECT source_id FROM description_sources WHERE job_id='linkedin:1009')")
        with self.assertRaisesRegex(ValueError, "Save a usable description"):
            self.store.enqueue("linkedin:1009")
        self.assertEqual(self.store.status()["counts"], {})
        self.store.enqueue("linkedin:1000")
        with self.store.connect() as conn:
            conn.execute("INSERT INTO job_consolidations VALUES ('linkedin:1011', 'linkedin:1000', 'description')")
        self.store.enqueue("linkedin:1011")
        with self.store.connect() as conn:
            self.assertEqual({r[0] for r in conn.execute("SELECT DISTINCT job_id FROM ai_work")}, {"linkedin:1000"})

    def test_metadata_and_language_evidence_survive_and_metadata_changes_invalidate_cache(self):
        with self.store.connect() as conn:
            before = self.store.prepare(conn, "linkedin:1000")
            self.assertEqual(before["input"]["sources"]["criteria.2"], "Job function: Finance and Sales")
            self.assertEqual(before["input"]["sources"]["description.2"], COPY.splitlines()[1])
            conn.execute("UPDATE jobs SET location='Zürich' WHERE job_id='linkedin:1000'")
            after = self.store.prepare(conn, "linkedin:1000")
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])
        payload = validate_evidence(JobExtraction.model_validate(fixture()), before["input"]["sources"])
        self.assertEqual(payload["description_language"], "de/en")
        evidence = payload["seniority"][0]["evidence"][0]
        self.assertEqual(before["input"]["sources"][evidence["source_ref"]][evidence["start"]:evidence["end"]], evidence["quote"])

    def test_model_receives_paragraphs_in_reading_order_and_version_upgrade_preserves_old_result(self):
        with self.store.connect() as conn:
            data = json.loads(conn.execute("SELECT data_json FROM description_extractions LIMIT 1").fetchone()[0])
            data["description_text"] = "\n".join(f"Paragraph {i}: original text and section context." for i in range(1, 13))
            conn.execute("UPDATE description_extractions SET data_json=?", (encode(data),))
        previous_contract = {**contract(), "schema_version": "1"}
        with patch.object(store_module, "contract", return_value=previous_contract):
            self.store.enqueue()
            work = self.store.claim(1)[0]
            old = fixture()
            old.pop("education")
            old["states"].pop("education")
            self.store.finish(work, payload=old)
        refs = [key for key in json.loads(work["input_json"])["sources"] if key.startswith("description.")]
        self.assertEqual(refs, [f"description.{i}" for i in range(1, 13)])
        self.assertEqual(self.store.enqueue(), 12)
        self.assertEqual(self.store.status()["counts"], {"queued": 12})
        result = self.store.job_result(work["job_id"])
        self.assertTrue(result["stale"])
        self.assertEqual(result["saved"]["contract"]["schema_version"], "1")
        self.assertNotIn("education", result["saved"]["fields"])
        self.assertEqual(result["status"], "queued")

    def test_rejects_invented_evidence_inconsistent_states_and_metadata_language_detection(self):
        with self.store.connect() as conn:
            sources = self.store.prepare(conn, "linkedin:1000")["input"]["sources"]
        payload = fixture()
        payload["requirements"][0]["evidence"][0]["quote"] = "Five years of Python required"
        with self.assertRaisesRegex(ValueError, "Evidence"):
            validate_evidence(JobExtraction.model_validate(payload), sources)
        payload = fixture()
        payload["states"]["requirements"] = "not_stated"
        with self.assertRaises(ValidationError):
            JobExtraction.model_validate(payload)
        payload = fixture()
        payload["description_languages"][0]["evidence"][0]["source_ref"] = "criteria.0"
        with self.assertRaises(ValidationError):
            JobExtraction.model_validate(payload)
        payload = fixture()
        payload["description_languages"][0]["code"] = "jpn"
        payload["description_languages"][1]["code"] = "fr"
        self.assertEqual(JobExtraction.model_validate(payload).description_languages[0].code, "jpn")

    def test_new_failure_keeps_previous_result_with_its_own_evidence(self):
        self.store.enqueue()
        work = self.store.claim(1)[0]
        sources = json.loads(work["input_json"])["sources"]
        self.store.finish(work, payload=validate_evidence(JobExtraction.model_validate(fixture()), sources))
        with self.store.connect() as conn:
            conn.execute("UPDATE jobs SET location='Zürich' WHERE job_id=?", (work["job_id"],))
        self.store.enqueue()
        with self.store.connect() as conn:
            conn.execute("UPDATE ai_work SET status='failed' WHERE fingerprint!=?", (work["fingerprint"],))
        result = self.store.job_result(work["job_id"])
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["stale"])
        self.assertEqual(result["saved"]["fields"]["description_language"], "de/en")
        self.assertEqual(result["input"]["sources"]["location"], "Berlin")

    def test_history_remains_readable_without_faking_evidence_or_modifying_reviews(self):
        with self.store.connect() as conn:
            conn.executescript("""CREATE TABLE parsed_descriptions(job_id TEXT,payload_json TEXT,model TEXT,version INTEGER,created_at TEXT);
                CREATE TABLE job_annotations(job_id TEXT,status TEXT,priority TEXT,updated_at TEXT);""")
            conn.execute("INSERT INTO parsed_descriptions VALUES ('linkedin:1000',?,'old-model',1,'2025-01-01')", (encode({"skills": ["Python"], "summary": "Saved summary"}),))
            conn.execute("INSERT INTO parsed_descriptions VALUES ('linkedin:1000',?,'old-model',1,'2025-02-01')", (encode({"dry_run": True}),))
            conn.execute("INSERT INTO job_annotations VALUES ('linkedin:1000','interesting','high','2025-01-01')")
        result = self.store.job_result("linkedin:1000")
        self.assertEqual(result["legacy"]["fields"]["skills"], ["Python"])
        self.assertIsNone(result["saved"])
        self.assertEqual(result["legacy_review"], {"status": "interesting", "priority": "high"})
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT is_favorite FROM jobs WHERE job_id='linkedin:1000'").fetchone()[0], 0)


class TestAIService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name)/"jobs.db"
        seed(self.path)
        async def requester(work):
            return response()
        self.service = AIService(self.path, requester=requester)

    async def asyncTearDown(self):
        await self.service.close()
        self.tmp.cleanup()

    async def test_all_saved_jobs_run_only_on_request_and_report_usage_and_api_readback(self):
        self.assertIsNone(self.service.task)
        self.assertEqual((await self.service.status())["counts"], {})
        queued = await self.service.queue()
        self.assertEqual(queued["added"], 12)
        await self.service.task
        self.assertEqual((await self.service.status())["counts"], {"succeeded": 12})
        with patch.object(api, "ai_service", self.service):
            result = await api.get_extraction("linkedin:1000", _=True)
        self.assertEqual(result["saved"]["fields"]["description_language"], "de/en")
        self.assertEqual((await self.service.queue())["added"], 0)
        await self.service.task
        self.assertEqual((await self.service.status())["usage"]["output_tokens"], 2400)

    async def test_single_job_endpoint_respects_pause_and_processes_only_the_selected_job(self):
        self.service.store.pause()
        with patch.object(api, "ai_service", self.service):
            result = await api.request_extraction("linkedin:1000", _=True)
        await self.service.task
        self.assertEqual(result["status"], "queued")
        self.assertTrue(result["queue"]["paused"])
        self.assertEqual((await self.service.status())["counts"], {"queued": 1})
        await self.service.control("resume")
        await self.service.task
        self.assertEqual((await self.service.status())["counts"], {"succeeded": 1})
        self.assertEqual((await self.service.status())["usage"]["output_tokens"], 200)

    async def test_single_job_endpoint_rejects_archived_favorite_without_starting_worker(self):
        with self.service.store.connect() as conn:
            conn.execute("UPDATE jobs SET archived_at='2026-09-06',is_favorite=1 WHERE job_id='linkedin:1000'")
        with patch.object(api, "ai_service", self.service):
            with self.assertRaises(api.HTTPException) as caught:
                await api.request_extraction("linkedin:1000", _=True)
            self.assertEqual(caught.exception.status_code, 400)
            self.assertIn("Only active jobs", caught.exception.detail)
            self.assertIsNone((await api.get_extraction("linkedin:1000", _=True))["saved"])
        self.assertIsNone(self.service.task)
        self.assertEqual(self.service.store.status()["counts"], {})

    async def test_archive_after_claim_prevents_request_and_late_completion(self):
        calls = []
        async def requested(work):
            calls.append(work["job_id"])
            return response()
        self.service.requester = requested
        self.service.store.enqueue("linkedin:1000")
        work = self.service.store.claim(1)[0]
        with self.service.store.connect() as conn:
            conn.execute("UPDATE jobs SET archived_at='2026-09-06' WHERE job_id=?", (work["job_id"],))
        await self.service.process(work)
        self.assertEqual(calls, [])
        self.service.store.finish(work, payload=fixture())
        result = self.service.store.job_result(work["job_id"])
        self.assertEqual(result["status"], "interrupted")
        self.assertIsNone(result["saved"])
        with self.service.store.connect() as conn:
            attempt = conn.execute("SELECT status,error_kind FROM ai_attempts").fetchone()
        self.assertEqual(tuple(attempt), ("interrupted", "inactive"))

    async def test_archive_during_request_retains_usage_without_publishing_or_retrying(self):
        async def archived_during_request(work):
            with self.service.store.connect() as conn:
                conn.execute("UPDATE jobs SET archived_at='2026-09-06' WHERE job_id=?", (work["job_id"],))
            return response()
        self.service.requester = archived_during_request
        self.service.store.enqueue("linkedin:1000")
        work = self.service.store.claim(1)[0]
        await self.service.process(work)
        self.assertIsNone(self.service.store.job_result(work["job_id"])["saved"])
        self.assertEqual(self.service.store.status()["counts"], {})
        self.assertEqual(self.service.store.status()["usage"]["output_tokens"], 200)
        self.assertEqual(self.service.store.retry_failed(), 0)
        with self.service.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM description_extractions WHERE extractor='openai_job'").fetchone()[0], 0)
            self.assertEqual(tuple(conn.execute("SELECT status,error_kind FROM ai_work").fetchone()), ("interrupted", "inactive"))
        # A transient failure arriving after an archive also must not schedule a retry.
        self.service.store.enqueue("linkedin:1001")
        work = self.service.store.claim(1)[0]
        with self.service.store.connect() as conn:
            conn.execute("UPDATE jobs SET relevance_outcome='excluded' WHERE job_id=?", (work["job_id"],))
        self.service.store.finish(work, error_kind="rate_limit", retry_delay=60, global_backoff=True)
        self.assertEqual(self.service.store.job_result(work["job_id"])["status"], "interrupted")
        self.assertEqual(self.service.store.status()["cooldown_until"], 0)

    async def test_capacity_retry_is_durable_and_applies_global_backoff(self):
        req = httpx.Request("POST", "https://api.openai.com/v1/responses")
        async def limited(work):
            raise openai.RateLimitError("capacity", response=httpx.Response(429, request=req, headers={"retry-after": "30"}), body={"code": "rate_limit_exceeded"})
        self.service.requester = limited
        self.service.store.enqueue()
        work = self.service.store.claim(1)[0]
        await self.service.process(work)
        self.assertEqual(self.service.store.status()["counts"]["retry_wait"], 1)
        self.assertEqual(self.service.store.claim(100), [])
        with self.service.store.connect() as conn:
            attempt = conn.execute("SELECT error_kind FROM ai_attempts").fetchone()[0]
        self.assertEqual(attempt, "rate_limit")

    async def test_incomplete_and_bad_quotes_are_not_published(self):
        for bad in (response(status="incomplete"), response()):
            if bad.status == "completed":
                payload = json.loads(bad.output_text)
                payload["requirements"][0]["evidence"][0]["quote"] = "Made up statement"
                bad.output_text = encode(payload)
            async def invalid(work):
                return bad
            self.service.requester = invalid
            self.service.store.enqueue()
            work = self.service.store.claim(1)[0]
            await self.service.process(work)
            result = self.service.store.job_result(work["job_id"])
            self.assertIsNone(result["saved"])
            if bad.status == "completed":
                self.assertEqual(result["rejected"]["requirements"][0]["evidence"][0]["quote"], "Made up statement")
                self.assertIn("Evidence does not match", result["error"])

    async def test_shutdown_preserves_work_and_single_coordinator_lock(self):
        with self.assertRaisesRegex(RuntimeError, "coordinator"):
            AIService(self.path, requester=self.service.requester)
        self.service.store.enqueue()
        self.service.store.claim(1)
        self.service.store.recover()
        self.assertTrue(self.service.store.status()["paused"])
        self.assertEqual(self.service.store.status()["counts"]["interrupted"], 1)

    async def test_rejected_business_output_retains_usage_and_full_draft(self):
        payload = fixture()
        payload["states"]["languages"] = "stated"
        async def invalid(work):
            return response(payload)
        self.service.requester = invalid
        self.service.store.enqueue()
        work = self.service.store.claim(1)[0]
        await self.service.process(work)
        result = self.service.store.job_result(work["job_id"])
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["saved"])
        self.assertEqual(result["rejected"]["output"], payload)
        self.assertEqual(self.service.store.status()["usage"]["output_tokens"], 200)
        with self.service.store.connect() as conn:
            attempt = conn.execute("SELECT response_id,estimated_cost_usd FROM ai_attempts WHERE work_id=?", (work["work_id"],)).fetchone()
        self.assertEqual(attempt[0], "resp_test")
        self.assertGreater(attempt[1], 0)

    async def test_sdk_sends_medium_flex_pydantic_without_output_cap(self):
        self.service.requester = None
        bodies = []
        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "resp_123", "created_at": 1, "object": "response", "status": "completed",
                "model": "gpt-5.4-mini", "service_tier": "flex", "output": [{"id": "msg_123", "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": encode(fixture()), "annotations": []}]}],
                "usage": {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 80}}})
        self.service.client = openai.AsyncOpenAI(api_key="test", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        self.service.store.enqueue()
        work = self.service.store.claim(1)[0]
        await self.service.process(work)
        self.assertEqual(bodies[0]["model"], "gpt-5.4-mini")
        self.assertEqual(bodies[0]["reasoning"], {"effort": "medium"})
        self.assertEqual(bodies[0]["service_tier"], "flex")
        self.assertNotIn("max_output_tokens", bodies[0])
        self.assertTrue(bodies[0]["text"]["format"]["strict"])
        self.assertEqual(bodies[0]["text"]["format"]["schema"], JobExtraction.model_json_schema())
        self.assertEqual(self.service.store.job_result(work["job_id"])["status"], "succeeded")

    async def test_async_pool_has_100_request_ceiling(self):
        await self.service.close()
        path = Path(self.tmp.name)/"pool.db"
        seed(path, count=101)
        reached = asyncio.Event()
        release = asyncio.Event()
        running = peak = 0
        async def waiting(work):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            if running == 100:
                reached.set()
            await release.wait()
            running -= 1
            return response()
        self.service = AIService(path, requester=waiting)
        await self.service.queue()
        await asyncio.wait_for(reached.wait(), timeout=10)
        self.assertEqual(peak, 100)
        self.assertEqual((await self.service.queue())["added"], 1)
        self.assertEqual(self.service.store.claim(1), [])
        release.set()
        await asyncio.wait_for(self.service.task, timeout=10)
        self.assertEqual((await self.service.status())["counts"], {"succeeded": 101})
        self.assertEqual(len((await self.service.status())["jobs"]), 20)
