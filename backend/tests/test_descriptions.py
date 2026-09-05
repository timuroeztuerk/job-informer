"""Description durability, replay, network boundaries, and queue recovery."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests
from fastapi import HTTPException
from pydantic import ValidationError

from backend import api
from backend.src.descriptions import linkedin
from backend.src.descriptions.linkedin import DescriptionError, FetchedSource, parse_description
from backend.src.descriptions.service import DescriptionService
from backend.src.descriptions.store import DescriptionStore
from backend.src.descriptions.legacy import LEGACY_EXTRACTOR
from backend.src.utils.database import JobDatabase
from backend.src.utils.database_backup import create_database_backup, verify_sqlite_database


def page(job_id="123", copy="Build <strong>reliable</strong> models."):
    return f'''<!doctype html><html><head><meta charset="utf-8">
    <link rel="canonical" href="https://www.linkedin.com/jobs/view/{job_id}/"></head>
    <body><h1 class="topcard__title">Data Scientist</h1>
    <div class="show-more-less-html__markup"><p>{copy}</p><ul><li>Python &amp; SQL</li><li>Zürich team</li></ul>
    <script>alert("untrusted")</script></div>
    <ul><li class="description__job-criteria-item"><h3 class="description__job-criteria-subheader">Employment type</h3>
    <span class="description__job-criteria-text">Full-time</span></li></ul>
    <aside>Similar jobs are not the original description.</aside></body></html>'''.encode()


class TestDescriptionParsing(unittest.TestCase):
    def test_expired_listing_redirect_is_unavailable_without_following_it(self):
        for location, expected in [
            ("https://de.linkedin.com/jobs/analyst-stellen?trk=expired_jd_redirect", "not_found"),
            ("/jobs/search?trk=expired_jd_redirect", "not_found"),
            ("https://www.linkedin.com/jobs/view/data-scientist-123", "blocked"),
            ("https://www.linkedin.com/login?trk=expired_jd_redirect", "blocked"),
            ("https://linkedin.com.example.org/jobs/search?trk=expired_jd_redirect", "blocked"),
        ]:
            response = Mock(status_code=301, headers={"Location": location})
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=None)
            with self.subTest(location=location), patch.object(linkedin.requests, "get", return_value=response) as get:
                with self.assertRaises(DescriptionError) as error:
                    linkedin.fetch_source("linkedin:123")
                self.assertEqual(error.exception.kind, expected)
                self.assertEqual(error.exception.http_status, 301)
                if expected == "not_found":
                    self.assertIsNone(error.exception.retry_after)
                self.assertEqual(get.call_count, 1)
                self.assertFalse(get.call_args.kwargs["allow_redirects"])
                response.iter_content.assert_not_called()

    def test_preserves_readable_text_and_explicit_criteria_without_page_chrome(self):
        parsed = parse_description(page(), "linkedin:123")
        self.assertEqual(parsed["description_text"], "Build reliable models.\n• Python & SQL\n• Zürich team")
        self.assertEqual(parsed["criteria"][0]["key"], "employment_type")
        self.assertEqual(parsed["criteria"][0]["evidence"]["text"], "Full-time")
        self.assertEqual(parsed["evidence"]["description_locator"], ".show-more-less-html__markup")

    def test_json_ld_requires_matching_identity_and_missing_is_not_empty_success(self):
        body = ('<script type="application/ld+json">' + json.dumps({"@graph": [
            {"@type": "JobPosting", "identifier": {"value": "456"}, "description": "Wrong job"},
            {"@type": "JobPosting", "identifier": {"value": "123"}, "description": "<p>Original role</p>"},
        ]}) + '</script>').encode()
        self.assertEqual(parse_description(body, "linkedin:123")["description_text"], "Original role")
        for body, kind in [(page("456"), "identity_mismatch"), (b"<title>Sign in | LinkedIn</title>", "blocked"),
                           (b'<div class="show-more-less-html__markup">Some unrelated text</div>', "identity_mismatch"),
                           (b"<h1>Other content</h1>", "parse")]:
            with self.subTest(kind=kind), self.assertRaises(DescriptionError) as error:
                parse_description(body, "linkedin:123")
            self.assertEqual(error.exception.kind, kind)

    def test_fetch_is_bounded_conditional_and_never_follows_redirects(self):
        response = Mock(status_code=200, headers={"Content-Type": "text/html", "ETag": '"v1"'})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.iter_content.return_value = [page()]
        with patch.object(linkedin.requests, "get", return_value=response) as get:
            fetched = linkedin.fetch_source("linkedin:123", {"etag": '"old"', "last_modified": "yesterday"})
        self.assertEqual(fetched.body, page())
        self.assertEqual(get.call_args.args[0], "https://www.linkedin.com/jobs/view/123/")
        self.assertIs(get.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(get.call_args.kwargs["headers"]["If-None-Match"], '"old"')
        response.status_code = 304
        with patch.object(linkedin.requests, "get", return_value=response):
            self.assertEqual(linkedin.fetch_source("linkedin:123", {"etag": '"v1"'}).status, 304)
        response.status_code = 200
        with patch.object(linkedin.requests, "get", return_value=response), patch.object(linkedin, "MAX_BODY_BYTES", 10):
            with self.assertRaises(DescriptionError) as error:
                linkedin.fetch_source("linkedin:123")
            self.assertEqual(error.exception.kind, "response_limit")
        for code, kind in [(429, "rate_limited"), (403, "blocked"), (302, "blocked"), (404, "not_found"), (503, "http")]:
            response.status_code = code
            with patch.object(linkedin.requests, "get", return_value=response) as get:
                with self.subTest(code=code), self.assertRaises(DescriptionError) as error:
                    linkedin.fetch_source("linkedin:123")
                self.assertEqual(error.exception.kind, kind)
                self.assertEqual(get.call_count, 1)
        with patch.object(linkedin.requests, "get", side_effect=requests.Timeout()):
            with self.assertRaises(DescriptionError) as error:
                linkedin.fetch_source("linkedin:123")
            self.assertEqual(error.exception.kind, "network")


class TestDescriptionWorkflow(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "jobs.db"
        self.db = JobDatabase(self.path)
        self.db.put_into_sql(pd.DataFrame([
            {"job_id": f"linkedin:{number}", "title": "Data Scientist", "company": f"Company {number}",
             "source": "LinkedIn", "location": "Berlin"} for number in (123, 456, 789)
        ]), observed_at="2026-09-05T08:00:00Z")
        self.fetcher = Mock(return_value=FetchedSource(200, page()))
        self.service = DescriptionService(str(self.path), fetcher=self.fetcher, interval=0)
        self.store = self.service.store

    def retrieve(self, *, refresh=False):
        self.store.enqueue(["linkedin:123"], refresh=refresh)
        self.service.process_one()
        return self.store.job_description("linkedin:123")

    def legacy_text(self, job_id="linkedin:123", text="Earlier description.\nPython & SQL, Zürich."):
        with self.store.connect() as conn:
            if "description" not in {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}:
                conn.execute("ALTER TABLE jobs ADD COLUMN description TEXT")
            conn.execute("UPDATE jobs SET description=? WHERE job_id=?", (text, job_id))
        return text

    def test_legacy_text_import_is_exact_idempotent_and_does_not_fabricate_fetches(self):
        text = self.legacy_text(text="  Earlier description.\n• Python & SQL, Zürich.\n<script>plain text</script>  ")
        self.legacy_text("linkedin:789", "FETCH_FAILED")
        self.db.set_job_flag("linkedin:123", True, "My review")
        self.db.set_job_favorite("linkedin:123", True)
        with self.store.connect() as conn:
            before = tuple(conn.execute("SELECT * FROM jobs WHERE job_id='linkedin:123'").fetchone())
        for _ in range(2):
            JobDatabase(self.path)
        result = self.store.job_description("linkedin:123")
        self.assertEqual(result["saved"]["source_kind"], "legacy_text")
        self.assertEqual(result["saved"]["data"]["description_text"], text)
        self.assertEqual(result["saved"]["data"]["criteria"], [])
        self.assertEqual(result["saved"]["extractor"], LEGACY_EXTRACTOR)
        self.assertEqual(result["source_versions"], 1)
        self.assertFalse(result["reparse_available"])
        self.assertEqual(result["attempts"], [])
        self.assertNotIn("linkedin:123", self.store.unfetched_candidates())
        self.assertEqual(self.store.enqueue(["linkedin:123"]), 0)
        self.assertEqual(self.store.queue_status()["saved_jobs"], 1)
        self.assertIsNone(self.store.job_description("linkedin:789")["saved"])
        self.assertIn("linkedin:789", self.store.unfetched_candidates())
        with self.store.connect() as conn:
            self.assertEqual(tuple(conn.execute("SELECT * FROM jobs WHERE job_id='linkedin:123'").fetchone()), before)
            self.assertEqual(conn.execute("SELECT body FROM description_sources").fetchone()[0], text.encode())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM description_extractions").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.fetcher.assert_not_called()

    def test_import_reuses_pending_fetches_but_keeps_explicit_refreshes(self):
        self.legacy_text()
        self.legacy_text("linkedin:456")
        self.store.enqueue(["linkedin:123"])
        self.store.enqueue(["linkedin:456"], refresh=True)
        self.store.enqueue(["linkedin:789"])
        JobDatabase(self.path)
        self.store.recover()
        cached = self.store.job_description("linkedin:123")["attempts"][0]
        self.assertEqual(cached["status"], "unchanged")
        self.assertIsNone(cached["started_at"])
        self.assertIsNone(cached["http_status"])
        self.assertIn("No network request", cached["error_message"])
        self.assertEqual(self.store.queue_status()["queued"], 2)
        self.store.resume()
        self.assertEqual(self.store.claim()["job_id"], "linkedin:456")
        self.fetcher.assert_not_called()

    def test_public_text_wins_over_import_and_failed_refresh_keeps_it(self):
        public = self.retrieve()["saved"]
        self.legacy_text()
        JobDatabase(self.path)
        self.assertEqual(self.store.job_description("linkedin:123")["saved"], public)
        self.assertEqual(self.store.job_description("linkedin:123")["source_versions"], 2)
        self.fetcher.side_effect = DescriptionError("not_found", "Expired", http_status=301)
        self.assertEqual(self.retrieve(refresh=True)["saved"], public)
        self.assertEqual(self.store.failed_candidates(), ["linkedin:123"])
        with patch.object(api, "description_service", self.service), patch.object(self.service, "start"):
            self.assertEqual(api.queue_descriptions(api.DescriptionBatchRequest(selection="retry"), _=True)["added"], 1)

    def test_legacy_text_is_readable_through_aliases_and_without_a_fetchable_id(self):
        self.legacy_text("linkedin:789", "\n \t ")
        with self.store.connect() as conn:
            conn.execute("""INSERT INTO jobs (job_id,title,company,location,source,url,description,
                scraped_at,first_seen_at,last_seen_at) VALUES
                ('de.linkedin.com/jobs/view/123','Data Scientist','Company 123','Berlin','LinkedIn',
                 'https://de.linkedin.com/jobs/view/123','Saved alias text','2026-07-12','2026-07-12','2026-07-12')""")
            conn.execute("UPDATE jobs SET job_id='old-unfetchable',description='Saved local text' WHERE job_id='linkedin:456'")
        JobDatabase(self.path)
        self.assertEqual(self.store.job_description("linkedin:123")["saved"]["data"]["description_text"], "Saved alias text")
        self.assertNotIn("linkedin:123", self.store.unfetched_candidates())
        self.assertEqual(self.store.enqueue(["linkedin:123", "old-unfetchable"]), 0)
        self.assertEqual(self.store.job_description("old-unfetchable")["saved"]["data"]["description_text"], "Saved local text")
        self.assertIsNone(self.store.job_description("linkedin:789")["saved"])
        with self.assertRaises(ValueError):
            self.store.enqueue(["old-unfetchable"], refresh=True)

    def test_upgrade_preserves_refresh_intent_and_newer_text_for_existing_queue(self):
        self.retrieve()
        self.store.enqueue(["linkedin:123"], refresh=True)
        with self.store.connect() as conn:
            conn.execute("ALTER TABLE description_fetches DROP COLUMN refresh")
        self.legacy_text()
        JobDatabase(self.path)
        self.store.recover()
        self.assertEqual(self.store.queue_status()["queued"], 1)
        self.store.resume()
        self.assertEqual(self.store.claim()["refresh"], 1)

    def test_cache_deduplication_history_and_annotations_survive_refresh_and_reopen(self):
        self.db.set_job_flag("linkedin:123", True, "Personal example")
        self.db.set_job_favorite("linkedin:123", True)
        first = self.retrieve()
        self.assertEqual(first["attempts"][0]["status"], "succeeded")
        self.assertEqual(first["saved"]["data"]["criteria"][0]["value"], "Full-time")
        self.assertEqual(self.store.enqueue(["linkedin:123"]), 0)
        self.assertFalse(self.service.process_one())
        same = self.retrieve(refresh=True)
        self.assertEqual(same["source_versions"], 1)
        self.assertEqual(same["attempts"][0]["status"], "unchanged")
        self.fetcher.return_value = FetchedSource(304)
        self.assertEqual(self.retrieve(refresh=True)["attempts"][0]["http_status"], 304)
        self.fetcher.return_value = FetchedSource(200, page(copy="A revised description."))
        changed = self.retrieve(refresh=True)
        self.assertEqual(changed["source_versions"], 2)
        self.assertIn("A revised description", changed["saved"]["data"]["description_text"])
        JobDatabase(self.path)
        self.assertEqual(DescriptionStore(str(self.path)).job_description("linkedin:123")["saved"], changed["saved"])
        with self.store.connect() as conn:
            self.assertEqual(tuple(conn.execute("SELECT is_flagged,flag_reason,is_favorite,archived_at,seen_count FROM jobs WHERE job_id='linkedin:123'").fetchone()), (1, "Personal example", 1, None, 1))
            self.assertEqual(conn.execute("SELECT body FROM description_sources WHERE source_id=?", (first["saved"]["source_id"],)).fetchone()[0], page())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM description_extractions").fetchone()[0], 2)

    def test_failed_refresh_keeps_last_good_text_and_retains_unparseable_source(self):
        original = self.retrieve()["saved"]
        self.fetcher.return_value = FetchedSource(200, b"<h1>Markup changed</h1>")
        failed = self.retrieve(refresh=True)
        self.assertEqual(failed["saved"], original)
        self.assertEqual(failed["attempts"][0]["error_kind"], "parse")
        self.assertEqual(failed["source_versions"], 2)
        self.assertEqual(self.store.latest_source("linkedin:123")["body"], b"<h1>Markup changed</h1>")
        self.assertEqual(self.store.failed_candidates(), ["linkedin:123"])

    def test_reparse_is_offline_versioned_and_idempotent(self):
        first = self.retrieve()
        self.fetcher.reset_mock()
        again = self.service.reparse("linkedin:123")
        self.assertEqual(first["saved"], again["saved"])
        self.fetcher.assert_not_called()
        source_id = first["saved"]["source_id"]
        data = {**first["saved"]["data"], "new_field": {"value": "Python", "evidence": "Python & SQL"}}
        with self.assertRaisesRegex(ValueError, "Bump the extractor version"):
            self.store.save_extraction(source_id, data)
        self.store.save_extraction(source_id, data, version="2.0.0", schema="2")
        self.store.save_extraction(source_id, {"skills": ["Python"]}, extractor="skills", version="1", schema="1")
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM description_extractions").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM description_sources").fetchone()[0], 1)

    def test_queue_claims_once_and_restart_requires_manual_resume(self):
        self.assertEqual(self.store.enqueue(["linkedin:123", "linkedin:456", "linkedin:123"]), 2)
        self.assertEqual(self.store.enqueue(["linkedin:123"]), 0)
        first = self.store.claim()
        self.assertEqual(first["job_id"], "linkedin:123")
        other = DescriptionStore(str(self.path))
        self.assertIsNone(other.claim())
        other.recover()
        self.assertEqual(other.job_description("linkedin:123")["attempts"][0]["status"], "interrupted")
        self.assertEqual(other.queue_status()["queued"], 1)
        self.assertTrue(other.queue_status()["paused"])
        self.assertIsNone(other.claim())
        # A stale worker cannot replace the interrupted attempt with success.
        other.finish(first["fetch_id"], "succeeded")
        self.assertEqual(other.job_description("linkedin:123")["attempts"][0]["status"], "interrupted")
        other.resume()
        self.assertEqual(other.claim()["job_id"], "linkedin:456")

    def test_rate_limit_pauses_remaining_queue_and_honors_cooldown(self):
        self.store.enqueue(["linkedin:123", "linkedin:456"])
        self.fetcher.side_effect = DescriptionError("rate_limited", "Slow down", http_status=429, retry_after="2099-01-01T00:00:00+00:00")
        self.service.process_one()
        state = self.store.queue_status()
        self.assertTrue(state["paused"])
        self.assertEqual((state["queued"], state["failed_jobs"]), (1, 1))
        self.assertFalse(self.service.process_one())
        with self.assertRaisesRegex(ValueError, "cooling down"):
            self.store.resume()
        self.assertEqual(self.fetcher.call_count, 1)

    def test_expired_refresh_preserves_saved_text_and_continues_to_the_next_job(self):
        original = self.retrieve()["saved"]
        self.store.enqueue(["linkedin:123", "linkedin:456"], refresh=True)
        self.fetcher.side_effect = [
            DescriptionError("not_found", "This posting has expired.", http_status=301),
            FetchedSource(200, page("456")),
        ]
        self.assertTrue(self.service.process_one())
        state = self.store.queue_status()
        self.assertFalse(state["paused"])
        self.assertIsNone(state["cooldown_until"])
        self.assertEqual(state["queued"], 1)
        result = self.store.job_description("linkedin:123")
        self.assertEqual(result["saved"], original)
        self.assertEqual(result["attempts"][0]["error_kind"], "not_found")
        self.assertTrue(self.service.process_one())
        self.assertEqual(self.store.job_description("linkedin:456")["attempts"][0]["status"], "succeeded")

    def test_batch_prioritizes_favorites_and_skips_previous_attempts_and_archived_jobs(self):
        with self.store.connect() as conn:
            conn.execute("UPDATE jobs SET archived_at='2026-09-05',is_favorite=1 WHERE job_id='linkedin:789'")
            conn.execute("UPDATE jobs SET archived_at='2026-09-05' WHERE job_id='linkedin:456'")
        self.assertEqual(self.store.unfetched_candidates(), ["linkedin:789", "linkedin:123"])
        self.store.enqueue(["linkedin:123"])
        self.assertEqual(self.store.unfetched_candidates(), ["linkedin:789"])

    def test_fetch_all_queues_beyond_batch_limits_without_repeating_attempts_or_resuming(self):
        self.retrieve()  # Saved descriptions stay out of the new queue.
        self.fetcher.reset_mock()
        self.db.put_into_sql(pd.DataFrame([
            {"job_id": f"linkedin:{number}", "title": "Data Scientist", "company": "ACME",
             "source": "LinkedIn", "location": "Berlin", "url": f"https://www.linkedin.com/jobs/view/{number}/"}
            for number in range(1000, 1035)
        ]), observed_at="2026-09-05T08:00:00Z")
        with self.store.connect() as conn:
            conn.execute("UPDATE jobs SET archived_at='2026-09-05' WHERE job_id IN ('linkedin:456','linkedin:789')")
            conn.execute("UPDATE jobs SET is_favorite=1 WHERE job_id='linkedin:789'")
        self.store.enqueue(["linkedin:1000", "linkedin:1001"])
        failed = self.store.claim()
        self.store.finish(failed["fetch_id"], "failed", error_kind="network")
        self.store.pause()

        with patch.object(api, "description_service", self.service), patch.object(self.service, "start"):
            result = api.queue_descriptions(api.DescriptionBatchRequest(selection="all"), _=True)
            self.assertEqual(result["added"], 34)
            self.assertEqual(result["queued"], 35)  # Includes the earlier pending job.
            self.assertTrue(result["paused"])
            self.assertEqual(api.queue_descriptions(api.DescriptionBatchRequest(), _=True)["added"], 0)
        with self.store.connect() as conn:
            queued = [r[0] for r in conn.execute("SELECT job_id FROM description_fetches WHERE status='queued' ORDER BY fetch_id")]
        self.assertEqual(queued[:2], ["linkedin:1001", "linkedin:789"])
        self.assertEqual(set(queued), {"linkedin:789", *(f"linkedin:{n}" for n in range(1001, 1035))})
        self.assertEqual(self.store.failed_candidates(), ["linkedin:1000"])
        self.assertFalse(self.service.process_one())
        self.fetcher.assert_not_called()

    def test_api_validation_and_routes_leave_missing_jobs_and_unknown_urls_unfetched(self):
        with patch.object(api, "description_service", self.service), patch.object(self.service, "start"):
            result = api.request_description("linkedin:123", api.DescriptionRequest(), _=True)
            self.assertEqual(result["attempts"][0]["status"], "queued")
            self.assertEqual(api.get_description("linkedin:123", _=True)["job_id"], "linkedin:123")
            with self.assertRaises(HTTPException) as error:
                api.request_description("linkedin:missing", api.DescriptionRequest(), _=True)
            self.assertEqual(error.exception.status_code, 404)
            with self.assertRaises(LookupError):
                self.store.enqueue(["linkedin:789", "linkedin:missing"])
            self.assertEqual(self.store.job_description("linkedin:789")["attempts"], [])
            with self.assertRaises(ValidationError):
                api.DescriptionBatchRequest(selection="retry", limit=10000)
            api.control_description_queue("pause", _=True)
            self.assertTrue(api.description_queue(_=True)["paused"])
        self.fetcher.assert_not_called()
        paths = [route.path for route in api.app.routes]
        self.assertLess(paths.index("/jobs/{job_id:path}/description"), paths.index("/jobs/{job_id:path}"))
        for job_id in ["https://example.com", "linkedin:123/../../", "linkedin:123?target=foo"]:
            with self.assertRaises(ValueError):
                linkedin.source_url(job_id)

    def test_worker_does_not_drop_a_request_arriving_during_empty_queue_exit(self):
        original_process = self.service.process_one
        inserted = False

        def process_with_arrival():
            nonlocal inserted
            result = original_process()
            if not result and not inserted:
                inserted = True
                self.service.enqueue(["linkedin:123"])
            return result

        with patch.object(self.service, "process_one", side_effect=process_with_arrival):
            self.service.start()
            thread = self.service._thread
            if thread:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
        self.assertEqual(self.fetcher.call_count, 1)
        self.assertEqual(self.store.queue_status()["saved_jobs"], 1)
        self.assertEqual(self.store.queue_status()["queued"], 0)

    def test_verified_backup_includes_original_bytes_extractions_and_fetch_history(self):
        self.retrieve()
        backup = create_database_backup(self.path)
        verify_sqlite_database(backup)
        copied = DescriptionStore(str(backup))
        self.assertEqual(copied.latest_source("linkedin:123")["body"], page())
        self.assertEqual(copied.job_description("linkedin:123"), self.store.job_description("linkedin:123"))
        with copied.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
