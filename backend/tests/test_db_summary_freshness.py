"""Database-summary freshness and UTC window tests."""

from __future__ import annotations

import os
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from backend.src.utils.database import JobDatabase
from backend.src.utils.db_summary import (
    compute_collection_freshness,
    compute_skill_gap_summary,
    compute_trend_summary,
)
from backend.src.utils.time_utils import utc_now


@contextmanager
def _temporary_timezone(name: str):
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def _insert_job(db: JobDatabase, job_id: str, observed_at: str) -> None:
    with db._get_connection() as conn:  # noqa: SLF001
        conn.execute(
            """
            INSERT INTO jobs (
                job_id,
                title,
                company,
                location,
                source,
                scraped_at,
                created_at,
                first_seen_at,
                last_seen_at,
                seen_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                f"Role {job_id}",
                "Example Co",
                "Berlin",
                "Test",
                observed_at,
                observed_at,
                observed_at,
                observed_at,
                1,
            ),
        )


def _insert_observation(db: JobDatabase, job_id: str, observed_at: str) -> None:
    with db._get_connection() as conn:  # noqa: SLF001
        conn.execute(
            """
            INSERT INTO job_observations (
                job_id,
                observed_at,
                title,
                company,
                location,
                source
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, observed_at, f"Role {job_id}", "Example Co", "Berlin", "Test"),
        )


class TestCollectionFreshness(unittest.TestCase):
    def test_empty_database_has_explicit_empty_state(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))

            freshness = compute_collection_freshness(db, as_of="2026-07-12T12:00:00Z")

            self.assertEqual(freshness["status"], "empty")
            self.assertIsNone(freshness["last_collected_at"])
            self.assertIsNone(freshness["age_days"])

    def test_freshness_thresholds_are_two_and_seven_days(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            observed_at = "2026-07-01T12:00:00Z"
            _insert_job(db, "job-threshold", observed_at)
            _insert_observation(db, "job-threshold", observed_at)

            self.assertEqual(
                compute_collection_freshness(db, as_of="2026-07-03T12:00:00Z")["status"],
                "fresh",
            )
            self.assertEqual(
                compute_collection_freshness(db, as_of="2026-07-05T12:00:00Z")["status"],
                "aging",
            )
            self.assertEqual(
                compute_collection_freshness(db, as_of="2026-07-09T12:00:00Z")["status"],
                "stale",
            )

            self.assertEqual(
                compute_collection_freshness(
                    db,
                    as_of="2026-07-09T12:00:00Z",
                    stale_after_days=10,
                )["status"],
                "aging",
            )

    def test_recent_empty_scrape_does_not_hide_stale_observations(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            observed_at = "2026-04-04T09:00:00Z"
            _insert_job(db, "job-stale", observed_at)
            _insert_observation(db, "job-stale", observed_at)
            with db._get_connection() as conn:  # noqa: SLF001
                conn.execute(
                    """
                    INSERT INTO scrape_runs (
                        scrape_run_id,
                        mode,
                        observed_at,
                        observed_jobs_count,
                        new_jobs_count
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    ("empty-recent-run", "run-once", "2026-07-12T08:00:00Z", 0, 0),
                )

            freshness = compute_collection_freshness(db, as_of="2026-07-12T12:00:00Z")

            self.assertEqual(freshness["status"], "stale")
            self.assertEqual(freshness["source"], "job_observation")
            self.assertEqual(freshness["latest_observation_at"], "2026-04-04T09:00:00+00:00")
            self.assertEqual(freshness["latest_scrape_at"], "2026-07-12T08:00:00+00:00")
            self.assertGreater(freshness["age_days"], 90)

    @unittest.skipUnless(hasattr(time, "tzset"), "requires POSIX timezone control")
    def test_legacy_berlin_local_observation_is_converted_before_freshness_and_trends(self) -> None:
        with _temporary_timezone("Europe/Berlin"), TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            # This is the pre-fix app format: 13:00 local wall time in Berlin,
            # representing 11:00 UTC in July.
            _insert_job(db, "job-local-naive", "2026-07-12T13:00:00")
            _insert_observation(db, "job-local-naive", "2026-07-12T13:00:00")

            freshness = compute_collection_freshness(db, as_of="2026-07-12T11:00:01Z")
            trend = compute_trend_summary(db, as_of="2026-07-12T11:00:01Z")

            self.assertEqual(freshness["last_collected_at"], "2026-07-12T11:00:00+00:00")
            self.assertAlmostEqual(freshness["age_days"], 1 / 86_400)
            self.assertEqual(trend["recent_window"]["jobs"], 1)

    @unittest.skipUnless(hasattr(time, "tzset"), "requires POSIX timezone control")
    def test_mixed_legacy_local_and_canonical_utc_observations_order_by_instant(self) -> None:
        with _temporary_timezone("Europe/Berlin"), TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            _insert_job(db, "job-legacy", "2026-07-12T13:00:00")  # 11:00Z
            _insert_observation(db, "job-legacy", "2026-07-12T13:00:00")
            _insert_job(db, "job-canonical", "2026-07-12T11:30:00+00:00")
            _insert_observation(db, "job-canonical", "2026-07-12T11:30:00+00:00")

            freshness = compute_collection_freshness(db, as_of="2026-07-12T12:00:00Z")

            self.assertEqual(freshness["last_collected_at"], "2026-07-12T11:30:00+00:00")

    @unittest.skipUnless(hasattr(time, "tzset"), "requires POSIX timezone control")
    def test_new_collection_timestamps_are_aware_utc_and_immediately_recent(self) -> None:
        with _temporary_timezone("Europe/Berlin"), TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            db.put_into_sql(
                pd.DataFrame(
                    [
                        {
                            "job_id": "job-new-utc",
                            "title": "Data Engineer",
                            "company": "Example Co",
                            "location": "Berlin",
                            "source": "Test",
                        }
                    ]
                )
            )

            with db._get_connection() as conn:  # noqa: SLF001
                job_row = conn.execute(
                    "SELECT scraped_at, created_at FROM jobs WHERE job_id = ?",
                    ("job-new-utc",),
                ).fetchone()
                observation_at = conn.execute(
                    "SELECT observed_at FROM job_observations WHERE job_id = ?",
                    ("job-new-utc",),
                ).fetchone()[0]
                run_row = conn.execute(
                    "SELECT observed_at, created_at FROM scrape_runs ORDER BY rowid DESC LIMIT 1"
                ).fetchone()

            for value in (*job_row, observation_at, *run_row):
                parsed = datetime.fromisoformat(value)
                self.assertEqual(parsed.utcoffset(), timedelta(0))
                self.assertTrue(value.endswith("+00:00"))

            trend = compute_trend_summary(db, as_of=utc_now() + timedelta(seconds=1))
            self.assertEqual(trend["recent_window"]["jobs"], 1)


class TestTrendWindows(unittest.TestCase):
    def test_windows_and_week_buckets_anchor_to_as_of_not_latest_job(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            _insert_job(db, "job-archive", "2026-04-04T09:00:00Z")
            _insert_job(db, "job-previous", "2026-06-02T09:00:00Z")
            _insert_job(db, "job-recent", "2026-07-02T09:00:00Z")

            trend = compute_trend_summary(
                db,
                window_days=30,
                week_buckets=4,
                as_of="2026-07-12T12:00:00Z",
            )

            self.assertEqual(trend["recent_window"]["start"], "2026-06-12T12:00:00+00:00")
            self.assertEqual(trend["recent_window"]["end"], "2026-07-12T12:00:00+00:00")
            self.assertEqual(trend["recent_window"]["jobs"], 1)
            self.assertEqual(trend["previous_window"]["start"], "2026-05-13T12:00:00+00:00")
            self.assertEqual(trend["previous_window"]["jobs"], 1)
            self.assertEqual(trend["weekly_job_counts"][-1]["week_start"], "2026-07-06T00:00:00+00:00")

    def test_stale_archive_does_not_create_fake_recent_window(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            _insert_job(db, "job-archive", "2026-04-04T09:00:00Z")

            trend = compute_trend_summary(db, window_days=30, as_of="2026-07-12T12:00:00Z")

            self.assertEqual(trend["recent_window"]["jobs"], 0)
            self.assertEqual(trend["previous_window"]["jobs"], 0)
            self.assertEqual(trend["recent_window"]["end"], "2026-07-12T12:00:00+00:00")


class TestSkillGapSummary(unittest.TestCase):
    def test_gaps_are_normalized_deduplicated_per_job_and_ignore_archived_jobs(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(db_path=str(Path(tmp_dir) / "jobs.db"))
            for job_id in ("job-one", "job-two", "job-archived"):
                _insert_job(db, job_id, "2026-07-10T09:00:00Z")

            with db._get_connection() as conn:  # noqa: SLF001
                conn.executemany(
                    "INSERT INTO job_annotations (job_id, skill_gaps) VALUES (?, ?)",
                    [
                        ("job-one", '[" Python ", "SQL", "python"]'),
                        ("job-two", '["python", "Kubernetes"]'),
                        ("job-archived", '["Python"]'),
                    ],
                )
                conn.execute(
                    "UPDATE jobs SET archived_at = ? WHERE job_id = ?",
                    ("2026-07-11T09:00:00Z", "job-archived"),
                )

            gaps = compute_skill_gap_summary(db)

            self.assertEqual(gaps["jobs_with_gaps"], 2)
            self.assertEqual(gaps["top_gaps"][0], {"name": "python", "count": 2, "percentage": 100.0})
            self.assertEqual(
                {item["name"]: item["count"] for item in gaps["top_gaps"]},
                {"python": 2, "kubernetes": 1, "sql": 1},
            )


if __name__ == "__main__":
    unittest.main()
