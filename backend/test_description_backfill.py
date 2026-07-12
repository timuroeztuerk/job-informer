from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from backend.src.agents.parser import DescriptionTools


_BACKFILL_COLUMNS = ["job_id", "url", "source", "title", "company", "scraped_at"]


def _batch(*rows: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=_BACKFILL_COLUMNS)


class _FakeBackfillDatabase:
    def __init__(self, batches: list[pd.DataFrame]):
        self._batches = list(batches)
        self.get_calls: list[tuple[int, bool]] = []
        self.update_calls: list[list[dict[str, str]]] = []
        self.failed_calls: list[list[str]] = []

    def get_jobs_missing_descriptions(self, limit: int, exclude_failed: bool) -> pd.DataFrame:
        self.get_calls.append((limit, exclude_failed))
        if not self._batches:
            return _batch()
        return self._batches.pop(0).copy()

    def update_job_descriptions(self, updates: list[dict[str, str]]) -> int:
        self.update_calls.append(list(updates))
        return len(updates)

    def mark_description_fetch_failed(self, job_ids: list[str]) -> int:
        self.failed_calls.append(list(job_ids))
        return len(job_ids)


class _FakeScraper:
    def __init__(
        self,
        db: _FakeBackfillDatabase,
        descriptions: dict[str, str | None] | None = None,
    ):
        self.db = db
        self.config = SimpleNamespace(request_delay=0.0)
        self.descriptions = descriptions or {}
        self.extract_calls: list[tuple[str, str]] = []
        self.progress_calls: list[tuple[int, int, str]] = []

    def _extract_job_description_from_url(self, source: str, url: str) -> str | None:
        self.extract_calls.append((source, url))
        return self.descriptions.get(url)

    def _update_progress(self, completed: int, total: int, prefix: str = "") -> None:
        self.progress_calls.append((completed, total, prefix))


def _description_tools(default_batch_size: int = 2) -> DescriptionTools:
    tools = DescriptionTools.__new__(DescriptionTools)
    tools.config = SimpleNamespace(scrape_descriptions_limit=default_batch_size)
    return tools


class TestDescriptionBackfillBatchLimits(unittest.TestCase):
    @patch("backend.src.agents.parser.time.sleep")
    def test_empty_backlog_stops_after_first_query(self, sleep) -> None:
        db = _FakeBackfillDatabase([_batch()])
        scraper = _FakeScraper(db)

        result = _description_tools().backfill_missing_descriptions(scraper, max_batches=None)

        self.assertTrue(result)
        self.assertEqual(db.get_calls, [(2, True)])
        self.assertEqual(db.update_calls, [])
        self.assertEqual(db.failed_calls, [])
        sleep.assert_not_called()

    @patch("backend.src.agents.parser.time.sleep")
    def test_one_batch_updates_successes_and_marks_failures(self, sleep) -> None:
        db = _FakeBackfillDatabase(
            [
                _batch(
                    {"job_id": "success", "url": "https://jobs.test/success", "source": "Test"},
                    {"job_id": "empty", "url": "https://jobs.test/empty", "source": "Test"},
                    {"job_id": "no-url", "url": "", "source": "Test"},
                )
            ]
        )
        scraper = _FakeScraper(
            db,
            {
                "https://jobs.test/success": "A useful job description",
                "https://jobs.test/empty": "",
            },
        )

        result = _description_tools().backfill_missing_descriptions(
            scraper,
            batch_size=3,
            max_batches=1,
        )

        self.assertTrue(result)
        self.assertEqual(db.get_calls, [(3, True)])
        self.assertEqual(
            db.update_calls,
            [[{"job_id": "success", "description": "A useful job description"}]],
        )
        self.assertEqual(db.failed_calls, [["empty", "no-url"]])
        self.assertEqual(len(scraper.extract_calls), 2)
        self.assertEqual(sleep.call_count, 2)

    @patch("backend.src.agents.parser.time.sleep")
    def test_unlimited_backfill_processes_multiple_batches_until_empty(self, sleep) -> None:
        urls = [f"https://jobs.test/{number}" for number in range(3)]
        batches = [
            _batch({"job_id": f"job-{number}", "url": url, "source": "Test"})
            for number, url in enumerate(urls)
        ]
        batches.append(_batch())
        db = _FakeBackfillDatabase(batches)
        scraper = _FakeScraper(
            db,
            {url: f"Description {number}" for number, url in enumerate(urls)},
        )

        result = _description_tools().backfill_missing_descriptions(
            scraper,
            batch_size=1,
            max_batches=None,
        )

        self.assertTrue(result)
        self.assertEqual(db.get_calls, [(1, True)] * 4)
        self.assertEqual(len(db.update_calls), 3)
        self.assertEqual(
            [call[0]["job_id"] for call in db.update_calls],
            ["job-0", "job-1", "job-2"],
        )
        self.assertEqual(len(scraper.extract_calls), 3)
        self.assertEqual(sleep.call_count, 6)

    @patch("backend.src.agents.parser.time.sleep")
    def test_finite_limit_stops_without_querying_an_extra_batch(self, sleep) -> None:
        urls = [f"https://jobs.test/{number}" for number in range(3)]
        db = _FakeBackfillDatabase(
            [
                _batch({"job_id": f"job-{number}", "url": url, "source": "Test"})
                for number, url in enumerate(urls)
            ]
        )
        scraper = _FakeScraper(
            db,
            {url: f"Description {number}" for number, url in enumerate(urls)},
        )

        result = _description_tools().backfill_missing_descriptions(
            scraper,
            batch_size=1,
            max_batches=2,
        )

        self.assertTrue(result)
        self.assertEqual(db.get_calls, [(1, True), (1, True)])
        self.assertEqual(len(db.update_calls), 2)
        self.assertEqual(
            [call[0]["job_id"] for call in db.update_calls],
            ["job-0", "job-1"],
        )
        self.assertEqual(len(scraper.extract_calls), 2)
        self.assertEqual(sleep.call_count, 3)

    @patch("backend.src.agents.parser.time.sleep")
    def test_non_positive_finite_limit_is_a_no_op(self, sleep) -> None:
        db = _FakeBackfillDatabase(
            [_batch({"job_id": "job-0", "url": "https://jobs.test/0", "source": "Test"})]
        )
        scraper = _FakeScraper(db, {"https://jobs.test/0": "Description"})

        for max_batches in (0, -1):
            with self.subTest(max_batches=max_batches):
                result = _description_tools().backfill_missing_descriptions(
                    scraper,
                    batch_size=1,
                    max_batches=max_batches,
                )
                self.assertTrue(result)

        self.assertEqual(db.get_calls, [])
        self.assertEqual(db.update_calls, [])
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
