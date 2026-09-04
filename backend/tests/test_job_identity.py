"""Provider job identity regression tests."""

from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from backend.src.utils.data_utils import (
    build_normalized_keys,
    normalize_job_url,
)
from backend.src.utils.database import JobDatabase


class TestLinkedInIdentity(unittest.TestCase):
    def test_real_linkedin_slug_urls_use_the_trailing_posting_id(self) -> None:
        urls = [
            "https://www.linkedin.com/jobs/view/4242424242/",
            "https://de.linkedin.com/jobs/view/senior-data-engineer-at-acme-4242424242",
            "www.linkedin.com/jobs/view/another-title-4242424242?trk=public_jobs_topcard-title",
        ]

        self.assertEqual(
            [normalize_job_url(url, "LinkedIn") for url in urls],
            ["linkedin:4242424242"] * len(urls),
        )

    def test_tracking_variations_share_one_database_identity(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            base_job = {
                "job_id": "https://www.linkedin.com/jobs/view/data-engineer-at-acme-4343434343",
                "title": "Data Engineer",
                "company": "ACME",
                "location": "Berlin",
                "source": "LinkedIn",
                "salary": "Not specified",
            }
            first = pd.DataFrame(
                [
                    {
                        **base_job,
                        "url": (
                            "https://www.linkedin.com/jobs/view/"
                            "data-engineer-at-acme-4343434343?trackingId=first"
                        ),
                    }
                ]
            )
            second = pd.DataFrame(
                [
                    {
                        **base_job,
                        "url": (
                            "https://www.linkedin.com/jobs/view/"
                            "renamed-data-role-at-acme-4343434343?refId=second"
                        ),
                    }
                ]
            )

            self.assertEqual(db.put_into_sql(first, observed_at="2026-07-10T09:00:00"), 1)
            self.assertEqual(db.put_into_sql(second, observed_at="2026-07-11T09:00:00"), 0)

            with closing(sqlite3.connect(db.db_path)) as conn, conn:
                job_rows = conn.execute(
                    "SELECT job_id, normalized_key, seen_count FROM jobs"
                ).fetchall()
                observation_count = conn.execute(
                    "SELECT COUNT(*) FROM job_observations"
                ).fetchone()[0]

            self.assertEqual(job_rows, [("linkedin:4343434343", "linkedin:4343434343", 2)])
            self.assertEqual(observation_count, 2)

    def test_legacy_raw_url_row_receives_repeat_observation(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            legacy_job_id = (
                "www.linkedin.com/jobs/view/data-scientist-at-acme-4545454545"
            )
            legacy_url = (
                "https://www.linkedin.com/jobs/view/"
                "data-scientist-at-acme-4545454545?trackingId=legacy"
            )
            first_seen = "2026-07-09T09:00:00"
            with closing(sqlite3.connect(db.db_path)) as conn, conn:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id,
                        title,
                        company,
                        location,
                        source,
                        url,
                        salary,
                        normalized_key,
                        scraped_at,
                        first_seen_at,
                        last_seen_at,
                        seen_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        legacy_job_id,
                        "Data Scientist",
                        "ACME",
                        "Berlin",
                        "LinkedIn",
                        legacy_url,
                        "Not specified",
                        legacy_url,
                        first_seen,
                        first_seen,
                        first_seen,
                        1,
                    ),
                )

            repeat = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:4545454545",
                        "title": "Data Scientist",
                        "company": "ACME",
                        "location": "Berlin",
                        "source": "LinkedIn",
                        "url": (
                            "https://de.linkedin.com/jobs/view/"
                            "data-scientist-at-acme-4545454545?refId=new"
                        ),
                        "salary": "Not specified",
                    }
                ]
            )

            inserted = db.put_into_sql(repeat, observed_at="2026-07-12T09:00:00")

            with closing(sqlite3.connect(db.db_path)) as conn, conn:
                job_rows = conn.execute(
                    "SELECT job_id, normalized_key, seen_count FROM jobs"
                ).fetchall()
                observation_rows = conn.execute(
                    "SELECT job_id, is_new FROM job_observations"
                ).fetchall()

            self.assertEqual(inserted, 0)
            self.assertEqual(
                job_rows,
                [(legacy_job_id, "linkedin:4545454545", 2)],
            )
            self.assertEqual(observation_rows, [(legacy_job_id, 0)])

    def test_missing_urls_do_not_become_nan_identity_keys(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "url": float("nan"),
                    "source": "LinkedIn",
                    "title": "Data Engineer",
                    "company": "ACME",
                },
                {
                    "url": pd.NA,
                    "source": "Indeed",
                    "title": "ML Engineer",
                    "company": "Beta",
                },
            ]
        )

        self.assertEqual(normalize_job_url(None, "LinkedIn"), "")
        self.assertEqual(normalize_job_url(float("nan"), "LinkedIn"), "")
        self.assertEqual(
            build_normalized_keys(frame).tolist(),
            ["linkedin|data engineer|acme", "indeed|ml engineer|beta"],
        )


if __name__ == "__main__":
    unittest.main()
