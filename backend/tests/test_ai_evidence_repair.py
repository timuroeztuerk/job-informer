"""Offline evidence repair: exact source spans, immutable usage, no retries."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.src.ai.evidence import resolve_evidence
from backend.src.ai.prompt import digest
from backend.src.ai.repair import repair_saved_outputs
from backend.src.ai.store import AIStore
from backend.tests.test_ai_extraction import fixture, seed


class TestEvidenceResolution(unittest.TestCase):
    def test_typography_returns_original_unicode_and_offsets(self):
        source = "🚀 We offer: hands‑on work, a cafe\u0301, and Straße experience."
        result = resolve_evidence("description.1", "• HANDS-ON work , a café, and STRASSE experience.",
                                  {"description.1": source})
        self.assertEqual(result["quote"], "hands‑on work, a cafe\u0301, and Straße experience.")
        self.assertEqual(source[result["start"]:result["end"]], result["quote"])
        self.assertEqual(result["repair"]["method"], "typography")

    def test_wrong_paragraph_requires_a_unique_source_in_the_same_family(self):
        sources = {"description.1": "About us", "description.2": "Python required", "title": "Data Scientist"}
        result = resolve_evidence("description.1", "Python required.", sources)
        self.assertEqual(result["source_ref"], "description.2")
        self.assertEqual(result["repair"]["method"], "source_reference")
        sources["description.3"] = "Python required"
        with self.assertRaises(ValueError):
            resolve_evidence("description.1", "Python required.", sources)
        with self.assertRaises(ValueError):
            resolve_evidence("description.1", "Data Scientist", sources)

    def test_single_spelling_error_with_context_preserves_source_and_audit(self):
        source = "You have experience in developing reliable analytics products."
        quote = "You have experinece in developing reliable analytics products."
        result = resolve_evidence("description.1", quote, {"description.1": source})
        self.assertEqual(result["quote"], source)
        self.assertEqual(result["repair"]["original_quote"], quote)
        self.assertEqual(result["repair"]["method"], "spelling")
        with self.assertRaises(ValueError):
            resolve_evidence("description.1", "experinece", {"description.1": source})
        with self.assertRaises(ValueError):
            resolve_evidence("description.1", quote, {"description.1": source + " " + source})

    def test_changed_claims_and_combined_passages_still_fail(self):
        pairs = [
            ("At least 3 years of professional experience", "At least 5 years of professional experience"),
            ("A university degree is not required", "A university degree is required"),
            ("Python or SQL experience is required", "Python and SQL experience is required"),
            ("Python required. SQL optional.", "Python and SQL required."),
            ("You work with Tableau in our team", "You work with Power BI in our team"),
            ("Previous management experience is required here", "Previous management experience is require here"),
        ]
        for source, quote in pairs:
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                resolve_evidence("description.1", quote, {"description.1": source})


class TestSavedOutputRepair(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "jobs.db"
        self.db = seed(self.path, count=2)
        self.store = AIStore(self.path)
        self.store.enqueue()
        self.items = []
        for work in self.store.claim(2):
            draft = fixture()
            original = {"source_ref": "description.1", "quote": "You develop reliable data products using Python"}
            draft["requirements"][0]["evidence"] = [original]
            self.store.finish(work, error_kind="output", message="Evidence mismatch", draft=draft,
                metadata={"request_id": "req_original", "response_id": "resp_original", "model": "saved-model",
                          "usage": {"output_tokens": 100}, "estimated_cost_usd": 0.001})
            self.items.append({"work_id": work["work_id"], "attempt_id": work["attempt_id"],
                "output_sha256": digest(draft), "corrections": [{"path": ["requirements", 0, "evidence", 0],
                    "original": original, "replacement": {"source_ref": "description.1", "quote": "Du entwickelst mit Python"}}]})
        self.plan = {"jobs": self.items}
        self.store.pause()

    def rows(self, table):
        with self.store.connect() as conn:
            return [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]

    def test_offline_repair_is_atomic_cached_and_preserves_costs_drafts_sources(self):
        tables = ("ai_attempts", "description_sources", "jobs", "ai_work", "description_extractions")
        before = {table: self.rows(table) for table in tables}
        self.assertEqual([r["status"] for r in repair_saved_outputs(self.path, self.plan)["results"]], ["ready"] * 2)
        self.assertEqual(before, {table: self.rows(table) for table in tables})
        result = repair_saved_outputs(self.path, self.plan, apply=True)
        self.assertEqual([r["status"] for r in result["results"]], ["repaired"] * 2)
        for table in tables[:3]:
            self.assertEqual(before[table], self.rows(table))
        self.assertEqual(self.store.status()["estimated_cost_usd"], 0.002)
        self.assertEqual(self.store.status()["counts"], {"succeeded": 2})
        self.assertTrue(self.store.status()["paused"])
        for row in result["results"]:
            saved = self.store.job_result(row["job_id"])
            self.assertFalse(saved["stale"])
            self.assertIsNone(saved["rejected"])
            self.assertEqual(saved["saved"]["fields"]["requirements"][0]["term"], "Python")
            self.assertEqual(saved["saved"]["metadata"]["local_repair"]["original_error"], "Evidence mismatch")
        after = self.rows("description_extractions")
        self.assertEqual([r["status"] for r in repair_saved_outputs(self.path, self.plan, apply=True)["results"]], ["already_repaired"] * 2)
        self.assertEqual(after, self.rows("description_extractions"))
        self.assertEqual(self.store.enqueue(), 0)
        self.assertEqual(self.store.retry_failed(), 0)
        self.assertEqual(before["ai_attempts"], self.rows("ai_attempts"))

    def test_invalid_second_replacement_leaves_the_whole_batch_untouched(self):
        before = self.rows("ai_work"), self.rows("description_extractions")
        plan = deepcopy(self.plan)
        plan["jobs"][1]["corrections"][0]["replacement"]["quote"] = "An invented requirement"
        with self.assertRaisesRegex(ValueError, "exact original source span"):
            repair_saved_outputs(self.path, plan, apply=True)
        self.assertEqual(before, (self.rows("ai_work"), self.rows("description_extractions")))

    def test_archived_favorites_and_filtered_jobs_cannot_be_repaired(self):
        self.db.archive_jobs(["linkedin:1000"], archived_reason="Not a fit")
        with self.store.connect() as conn:
            conn.execute("UPDATE jobs SET is_favorite=1 WHERE job_id='linkedin:1000'")
            conn.execute("UPDATE jobs SET relevance_outcome='excluded' WHERE job_id='linkedin:1001'")
        for item in self.items:
            with self.subTest(work_id=item["work_id"]), self.assertRaisesRegex(ValueError, "active job"):
                repair_saved_outputs(self.path, {"jobs": [item]}, apply=True)
        self.assertEqual(len(self.rows("description_extractions")), 2)

    def test_unpaused_or_changed_attempt_draft_is_rejected(self):
        self.store.resume()
        with self.assertRaisesRegex(ValueError, "Pause AI extraction"):
            repair_saved_outputs(self.path, self.plan, apply=True)
        self.store.pause()
        for field, value in (("attempt_id", 9999), ("output_sha256", "changed")):
            plan = deepcopy(self.plan)
            plan["jobs"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "changed since review"):
                repair_saved_outputs(self.path, plan, apply=True)

    def test_explicit_unsupported_language_removal_preserves_other_claims(self):
        plan = deepcopy(self.plan)
        language = fixture()["description_languages"][1]
        plan["jobs"][0]["remove_description_languages"] = [{"original": language, "reason": "Reviewed unsupported language claim"}]
        repair_saved_outputs(self.path, plan, apply=True)
        with self.store.connect() as conn:
            row = conn.execute("SELECT e.data_json FROM ai_work w JOIN description_extractions e USING(extraction_id) WHERE w.work_id=?", (self.items[0]["work_id"],)).fetchone()
        document = json.loads(row[0])
        self.assertEqual(document["fields"]["description_language"], "de")
        self.assertEqual(document["metadata"]["local_repair"]["removed_description_languages"][0]["original"], language)
        self.assertEqual(document["fields"]["languages"], fixture()["languages"])
