"""Conservative cleanup and canonical posting identity tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from backend.src.agents.job_scraper import JobScraper
from backend.src.utils.database import JobDatabase


class _ConservativeFilterConfig:
    @staticmethod
    def get_unwanted_keywords_list() -> list[str]:
        return []

    @staticmethod
    def get_unwanted_companies_list() -> list[str]:
        return []


def _cleanup_scraper(db: JobDatabase) -> JobScraper:
    scraper = JobScraper.__new__(JobScraper)
    scraper.db = db
    scraper.config = _ConservativeFilterConfig()
    return scraper


def _job(*, job_id: str, url: str, source: str, location: str) -> dict[str, str]:
    return {
        "job_id": job_id,
        "title": "Platform Engineer",
        "company": "Example Systems",
        "location": location,
        "source": source,
        "url": url,
        "salary": "Not specified",
        "description": "Build and operate the platform.",
    }


class TestCleanupPostingIdentity(unittest.TestCase):
    def test_same_title_company_source_with_distinct_posting_ids_stay_active(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            jobs = pd.DataFrame(
                [
                    _job(
                        job_id="linkedin:6111111111",
                        url="https://www.linkedin.com/jobs/view/platform-engineer-6111111111",
                        source="LinkedIn",
                        location="Berlin",
                    ),
                    _job(
                        job_id="linkedin:6222222222",
                        url="https://www.linkedin.com/jobs/view/platform-engineer-6222222222",
                        source="LinkedIn",
                        location="Munich",
                    ),
                    _job(
                        job_id="indeed:berlin-posting",
                        url="https://de.indeed.com/viewjob?jk=berlin-posting",
                        source="Indeed",
                        location="Berlin",
                    ),
                    _job(
                        job_id="indeed:munich-posting",
                        url="https://de.indeed.com/viewjob?jk=munich-posting",
                        source="Indeed",
                        location="Munich",
                    ),
                ]
            )

            self.assertEqual(db.put_into_sql(jobs, observed_at="2026-07-12T09:00:00Z"), 4)
            self.assertTrue(_cleanup_scraper(db).purge_unwanted_jobs())

            with db._get_connection() as conn:
                rows = conn.execute(
                    "SELECT job_id, location, archived_at FROM jobs ORDER BY job_id"
                ).fetchall()
                decision_count = conn.execute("SELECT COUNT(*) FROM filter_decisions").fetchone()[0]

            self.assertEqual(
                [(row[0], row[1]) for row in rows],
                [
                    ("indeed:berlin-posting", "Berlin"),
                    ("indeed:munich-posting", "Munich"),
                    ("linkedin:6111111111", "Berlin"),
                    ("linkedin:6222222222", "Munich"),
                ],
            )
            self.assertTrue(all(row[2] is None for row in rows))
            self.assertEqual(decision_count, 0)

    def test_tracking_url_variants_still_consolidate_before_cleanup(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            first = pd.DataFrame(
                [
                    _job(
                        job_id="https://www.linkedin.com/jobs/view/platform-engineer-6333333333",
                        url=(
                            "https://www.linkedin.com/jobs/view/"
                            "platform-engineer-at-example-6333333333?trackingId=first"
                        ),
                        source="LinkedIn",
                        location="Berlin",
                    )
                ]
            )
            repeat = pd.DataFrame(
                [
                    _job(
                        job_id="linkedin:6333333333",
                        url=(
                            "https://de.linkedin.com/jobs/view/"
                            "renamed-platform-role-6333333333?refId=second"
                        ),
                        source="LinkedIn",
                        location="Berlin",
                    )
                ]
            )

            self.assertEqual(db.put_into_sql(first, observed_at="2026-07-11T09:00:00Z"), 1)
            self.assertEqual(db.put_into_sql(repeat, observed_at="2026-07-12T09:00:00Z"), 0)
            self.assertTrue(_cleanup_scraper(db).purge_unwanted_jobs())

            with db._get_connection() as conn:
                rows = conn.execute(
                    "SELECT job_id, normalized_key, seen_count, archived_at FROM jobs"
                ).fetchall()

            self.assertEqual(
                [tuple(row) for row in rows],
                [("linkedin:6333333333", "linkedin:6333333333", 2, None)],
            )


if __name__ == "__main__":
    unittest.main()
