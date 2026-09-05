"""Output, source continuity, pilot isolation, and async queue behavior; no paid evals."""

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
from backend.src.ai.models import JobExtraction, validate_evidence
from backend.src.ai.prompt import encode
from backend.src.ai.service import AIService
from backend.src.ai.store import AIStore
from backend.src.utils.database import JobDatabase


COPY = "Du entwickelst mit Python zuverlässige Datenprodukte und arbeitest mit unserem Team an Analysen.\nWe offer flexible working hours and a collaborative environment."


def fixture():
    return {"description_languages": [
        {"code": "de", "evidence": [{"source_ref": "description.1", "quote": "Du entwickelst mit Python"}]},
        {"code": "en", "evidence": [{"source_ref": "description.2", "quote": "We offer flexible working hours"}]},
    ], "states": {"requirements": "stated", "languages": "not_stated", "experience": "not_stated",
                   "work_arrangement": "not_stated", "responsibilities": "not_stated", "seniority": "stated", "employment_type": "stated"},
        "requirements": [{"term": "Python", "category": "programming_language", "strength": "mentioned", "alternative_group": None,
                          "evidence": [{"source_ref": "description.1", "quote": "Du entwickelst mit Python"}]}],
        "languages": [], "experience": [], "work_arrangement": [], "responsibilities": [],
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
                           output_parsed=JobExtraction.model_validate(payload or fixture()))


class TestAIStore(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)/"jobs.db"
        self.db = seed(self.path)
        self.store = AIStore(self.path)

    def test_pilot_is_frozen_and_removing_limit_allows_remaining_jobs(self):
        self.assertEqual(self.store.enqueue(), 10)
        first = {item["job_id"] for item in self.store.status()["jobs"]}
        with self.store.connect() as conn:
            conn.execute("UPDATE jobs SET is_favorite=1 WHERE job_id='linkedin:1011'")
        self.store.recover()
        self.assertEqual(self.store.enqueue(), 0)
        self.assertEqual({item["job_id"] for item in self.store.status()["jobs"]}, first)
        with patch.object(store_module, "PILOT_LIMIT", None):
            self.assertEqual(self.store.enqueue(), 2)
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_work").fetchone()[0], 12)

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

    async def test_pilot_runs_once_and_reports_usage_and_api_readback(self):
        queued = await self.service.queue()
        self.assertEqual(queued["added"], 10)
        await self.service.task
        self.assertEqual((await self.service.status())["counts"], {"succeeded": 10})
        with patch.object(api, "ai_service", self.service):
            result = await api.get_extraction("linkedin:1000", _=True)
        self.assertEqual(result["saved"]["fields"]["description_language"], "de/en")
        self.assertEqual((await self.service.queue())["added"], 0)
        await self.service.task
        self.assertEqual((await self.service.status())["usage"]["output_tokens"], 2000)

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
                bad.output_parsed.requirements[0].evidence[0].quote = "Made up statement"
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
        with patch.object(store_module, "PILOT_LIMIT", None):
            await self.service.queue()
        await asyncio.wait_for(reached.wait(), timeout=10)
        self.assertEqual(peak, 100)
        self.assertEqual(self.service.store.claim(1), [])
        release.set()
        await asyncio.wait_for(self.service.task, timeout=10)
        self.assertEqual((await self.service.status())["counts"], {"succeeded": 101})
