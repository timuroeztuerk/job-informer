"""API-level relevance filters and provenance detail tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backend import api
from backend.src.utils.database import JobDatabase


def _list_jobs(**overrides: object) -> dict:
    arguments = {
        "limit": 50,
        "offset": 0,
        "search": None,
        "location": None,
        "source": None,
        "company": None,
        "role_family": None,
        "query_group": None,
        "archived": "exclude",
        "date_from": None,
        "date_to": None,
        "sort": "scraped_at_desc",
        "_": True,
    }
    arguments.update(overrides)
    return api.list_jobs(**arguments)  # type: ignore[arg-type]


class TestApiRelevance(unittest.TestCase):
    def test_role_and_query_filters_and_job_provenance(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "jobs.db"
            db = JobDatabase(path)
            run_id = db.start_scrape_run(
                api_run_id="api-run",
                keywords=["Data Scientist", "Data Analyst"],
                locations=["Berlin"],
                observed_at="2026-09-04T08:00:00Z",
            )
            reports = [
                {
                    "keyword": term,
                    "location": "Berlin",
                    "query_group_key": key,
                    "query_group_name": name,
                    "page_offsets": [0, 25],
                    "pages_attempted": 2,
                    "pages_completed": 2,
                    "valid_jobs": 1,
                    "stop_reason": "page_limit",
                }
                for term, key, name in (
                    ("Data Scientist", "data_science", "Data science"),
                    ("Data Analyst", "analytics_bi", "Analytics and BI"),
                )
            ]
            db.record_collection_queries(run_id, reports)
            db.put_into_sql(
                pd.DataFrame(
                    [
                        {
                            "job_id": "linkedin:1",
                            "title": "Data Scientist",
                            "company": "ACME",
                            "location": "Berlin",
                            "source": "LinkedIn",
                            "url": "https://www.linkedin.com/jobs/view/1/",
                        },
                        {
                            "job_id": "linkedin:2",
                            "title": "Data Analyst",
                            "company": "Beta",
                            "location": "Berlin",
                            "source": "LinkedIn",
                            "url": "https://www.linkedin.com/jobs/view/2/",
                        },
                    ]
                ),
                scrape_run_id=run_id,
                observed_at="2026-09-04T08:00:00Z",
            )
            db.record_job_query_matches(
                run_id,
                [
                    {
                        "job_id": "linkedin:1",
                        "query_text": "Data Scientist",
                        "query_location": "Berlin",
                        "query_page_offset": 25,
                    },
                    {
                        "job_id": "linkedin:2",
                        "query_text": "Data Analyst",
                        "query_location": "Berlin",
                        "query_page_offset": 0,
                    },
                ],
            )
            db.save_relevance_evaluations(
                [
                    {
                        "job_id": "linkedin:1",
                        "outcome": "target",
                        "role_family": "data_science",
                        "reason": "Kept as data science",
                        "ruleset_version": "test",
                    },
                    {
                        "job_id": "linkedin:2",
                        "outcome": "target",
                        "role_family": "analytics_bi",
                        "reason": "Kept as analytics BI",
                        "ruleset_version": "test",
                    },
                ]
            )

            settings = api.AppSettings(
                db_path=str(path),
                run_store_db_path=str(path),
                api_token="",
                max_log_bytes=30000,
                frontend_dist_dir=Path(tmp_dir) / "dist",
            )
            with patch.object(api, "APP_SETTINGS", settings), patch.object(api, "job_db", db):
                by_role = _list_jobs(role_family="analytics_bi")
                by_query = _list_jobs(query_group="data_science")
                detail = api.get_job("linkedin:1", _=True)
                run_queries = api._query_coverage_for_api_run("api-run")  # noqa: SLF001
                api.delete_job("linkedin:2", _=True)
                archived = _list_jobs(archived="only")
                api.restore_job("linkedin:2", _=True)

            self.assertEqual([item["job_id"] for item in by_role["items"]], ["linkedin:2"])
            self.assertEqual([item["job_id"] for item in by_query["items"]], ["linkedin:1"])
            self.assertEqual(by_query["items"][0]["query_groups"], ["data_science"])
            self.assertEqual(detail["query_matches"][0]["query_text"], "Data Scientist")
            self.assertEqual(detail["query_matches"][0]["first_page_offset"], 25)
            self.assertEqual(len(run_queries), 2)
            self.assertEqual(run_queries[0]["page_offsets"], [0, 25])
            self.assertEqual([item["job_id"] for item in archived["items"]], ["linkedin:2"])
            with db._get_connection() as conn:  # noqa: SLF001
                restored_state = conn.execute(
                    "SELECT archived_at, relevance_outcome FROM jobs WHERE job_id = 'linkedin:2'"
                ).fetchone()
            self.assertEqual(tuple(restored_state), (None, "manual_keep"))


if __name__ == "__main__":
    unittest.main()
