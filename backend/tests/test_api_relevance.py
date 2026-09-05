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
        "relevance_outcome": None,
        "favorite": False,
        "archived": "exclude",
        "date_from": None,
        "date_to": None,
        "sort": "scraped_at_desc",
        "_": True,
    }
    arguments.update(overrides)
    return api.list_jobs(**arguments)  # type: ignore[arg-type]


class TestApiRelevance(unittest.TestCase):
    def test_favorite_state_is_independent_from_archive_state(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "jobs.db"
            db = JobDatabase(path)
            db.put_into_sql(
                pd.DataFrame(
                    [{
                        "job_id": "linkedin:favorite-archive",
                        "title": "Data Scientist",
                        "company": "ACME",
                        "location": "Berlin",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/favorite-archive/",
                    }]
                ),
                observed_at="2026-09-04T08:00:00Z",
            )
            settings = api.AppSettings(
                db_path=str(path),
                run_store_db_path=str(path),
                api_token="",
                max_log_bytes=30000,
                frontend_dist_dir=Path(tmp_dir) / "dist",
            )

            with patch.object(api, "APP_SETTINGS", settings), patch.object(api, "job_db", db):
                api.update_job_favorite(
                    "linkedin:favorite-archive",
                    api.FavoriteUpdate(is_favorite=True),
                    _=True,
                )
                api.delete_job("linkedin:favorite-archive", _=True)
                archived_detail = api.get_job("linkedin:favorite-archive", _=True)
                archived_favorites = _list_jobs(favorite=True, archived="only")

                api.update_job_favorite(
                    "linkedin:favorite-archive",
                    api.FavoriteUpdate(is_favorite=False),
                    _=True,
                )
                unfavorited_detail = api.get_job("linkedin:favorite-archive", _=True)

                api.update_job_favorite(
                    "linkedin:favorite-archive",
                    api.FavoriteUpdate(is_favorite=True),
                    _=True,
                )
                api.restore_job("linkedin:favorite-archive", _=True)
                restored_detail = api.get_job("linkedin:favorite-archive", _=True)
                active_favorites = _list_jobs(favorite=True)

            self.assertIs(archived_detail["is_favorite"], True)
            self.assertIsNotNone(archived_detail["archived_at"])
            self.assertEqual(
                [item["job_id"] for item in archived_favorites["items"]],
                ["linkedin:favorite-archive"],
            )
            self.assertIs(unfavorited_detail["is_favorite"], False)
            self.assertIsNotNone(unfavorited_detail["archived_at"])
            self.assertIs(restored_detail["is_favorite"], True)
            self.assertIsNone(restored_detail["archived_at"])
            self.assertEqual(
                [item["job_id"] for item in active_favorites["items"]],
                ["linkedin:favorite-archive"],
            )

    def test_run_scope_comparison_measures_yield_overlap_and_request_health(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "jobs.db"
            db = JobDatabase(path)
            run_id = db.start_scrape_run(
                api_run_id="scope-run",
                keywords=["Data Scientist", "Data Analyst"],
                locations=["Germany", "Switzerland", "Berlin", "München"],
                observed_at="2026-09-04T08:00:00Z",
            )
            db.record_collection_queries(
                run_id,
                [
                    {
                        "keyword": "Data Scientist",
                        "location": "Germany",
                        "query_group_key": "data_science",
                        "pages_attempted": 2,
                        "pages_completed": 2,
                        "request_failures": 0,
                    },
                    {
                        "keyword": "Data Analyst",
                        "location": "Switzerland",
                        "query_group_key": "analytics_bi",
                        "pages_attempted": 2,
                        "pages_completed": 1,
                        "request_failures": 1,
                    },
                    {
                        "keyword": "Data Scientist",
                        "location": "Berlin",
                        "query_group_key": "data_science",
                        "pages_attempted": 2,
                        "pages_completed": 2,
                        "request_failures": 0,
                    },
                    {
                        "keyword": "Data Analyst",
                        "location": "München",
                        "query_group_key": "analytics_bi",
                        "pages_attempted": 1,
                        "pages_completed": 1,
                        "request_failures": 2,
                    },
                ],
            )
            db.put_into_sql(
                pd.DataFrame(
                    [
                        {
                            "job_id": f"linkedin:{index}",
                            "title": "Data Scientist",
                            "company": f"Company {index}",
                            "location": "Germany",
                            "source": "LinkedIn",
                            "url": f"https://www.linkedin.com/jobs/view/{index}/",
                        }
                        for index in range(1, 5)
                    ]
                ),
                scrape_run_id=run_id,
                observed_at="2026-09-04T08:00:00Z",
            )
            db.record_job_query_matches(
                run_id,
                [
                    {"job_id": "linkedin:1", "query_text": "Data Scientist", "query_location": "Germany"},
                    {"job_id": "linkedin:2", "query_text": "Data Scientist", "query_location": "Germany"},
                    {"job_id": "linkedin:3", "query_text": "Data Analyst", "query_location": "Switzerland"},
                    {"job_id": "linkedin:2", "query_text": "Data Scientist", "query_location": "Berlin"},
                    {"job_id": "linkedin:3", "query_text": "Data Analyst", "query_location": "München"},
                    {"job_id": "linkedin:4", "query_text": "Data Scientist", "query_location": "Berlin"},
                ],
            )

            settings = api.AppSettings(
                db_path=str(path),
                run_store_db_path=str(path),
                api_token="",
                max_log_bytes=30000,
                frontend_dist_dir=Path(tmp_dir) / "dist",
            )
            with patch.object(api, "APP_SETTINGS", settings):
                comparison = api._scope_comparison_for_api_run("scope-run")  # noqa: SLF001

        self.assertIsNotNone(comparison)
        assert comparison is not None
        self.assertEqual(comparison["countrywide"]["unique_jobs"], 3)
        self.assertEqual(comparison["countrywide"]["pages_completed"], 3)
        self.assertEqual(comparison["countrywide"]["pages_attempted"], 4)
        self.assertEqual(comparison["countrywide"]["page_completion_percent"], 75.0)
        self.assertEqual(comparison["countrywide"]["request_failures"], 1)
        self.assertEqual(comparison["cities"]["unique_jobs"], 3)
        self.assertEqual(comparison["cities"]["pages_completed"], 3)
        self.assertEqual(comparison["cities"]["pages_attempted"], 3)
        self.assertEqual(comparison["cities"]["request_failures"], 2)
        self.assertEqual(comparison["shared_jobs"], 2)
        self.assertEqual(comparison["countrywide_only_jobs"], 1)
        self.assertEqual(comparison["city_only_jobs"], 1)
        self.assertEqual(comparison["city_jobs_covered_by_countrywide_percent"], 66.7)
        self.assertEqual(comparison["per_city"], [
            {"location": "Berlin", "unique_jobs": 2, "shared_jobs": 1, "city_only_jobs": 1},
            {"location": "München", "unique_jobs": 1, "shared_jobs": 1, "city_only_jobs": 0},
        ])

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
                favorite_update = api.update_job_favorite(
                    "linkedin:1",
                    api.FavoriteUpdate(is_favorite=True),
                    _=True,
                )
                by_role = _list_jobs(role_family="analytics_bi")
                by_query = _list_jobs(query_group="data_science")
                by_favorite = _list_jobs(favorite=True)
                detail = api.get_job("linkedin:1", _=True)
                run_queries = api._query_coverage_for_api_run("api-run")  # noqa: SLF001
                scope_comparison = api._scope_comparison_for_api_run("api-run")  # noqa: SLF001
                api.delete_job("linkedin:2", _=True)
                archived = _list_jobs(archived="only")
                archived_stats = api.job_stats(_=True)
                api.restore_job("linkedin:2", _=True)
                restored_stats = api.job_stats(_=True)

            # The compact stats endpoint still supplies the live filter options
            # and database status used by the Jobs page.
            self.assertEqual(archived_stats["companies_list"], ["ACME"])
            self.assertEqual(archived_stats["role_families_list"], ["data_science"])
            self.assertEqual(archived_stats["query_groups_list"], [{"key": "data_science", "name": "Data science"}])
            self.assertEqual(archived_stats["database"]["active_jobs"], 1)
            self.assertEqual(restored_stats["companies_list"], ["ACME", "Beta"])
            # Manual restoration clears the automatic role classification.
            self.assertEqual(restored_stats["role_families_list"], ["data_science"])
            self.assertEqual(len(restored_stats["query_groups_list"]), 2)
            self.assertEqual(restored_stats["database"]["active_jobs"], 2)
            self.assertEqual(restored_stats["collection_freshness"]["source"], "job_observation")

            self.assertEqual([item["job_id"] for item in by_role["items"]], ["linkedin:2"])
            self.assertIs(by_role["items"][0]["is_favorite"], False)
            self.assertEqual([item["job_id"] for item in by_query["items"]], ["linkedin:1"])
            self.assertIs(by_query["items"][0]["is_favorite"], True)
            self.assertEqual(favorite_update, {"job_id": "linkedin:1", "is_favorite": True})
            self.assertEqual([item["job_id"] for item in by_favorite["items"]], ["linkedin:1"])
            self.assertEqual(by_query["items"][0]["query_groups"], ["data_science"])
            self.assertIs(detail["is_favorite"], True)
            self.assertEqual(detail["query_matches"][0]["query_text"], "Data Scientist")
            self.assertEqual(detail["query_matches"][0]["first_page_offset"], 25)
            self.assertEqual(len(run_queries), 2)
            self.assertEqual(run_queries[0]["page_offsets"], [0, 25])
            self.assertIsNone(scope_comparison)
            self.assertEqual([item["job_id"] for item in archived["items"]], ["linkedin:2"])
            with db._get_connection() as conn:  # noqa: SLF001
                restored_state = conn.execute(
                    "SELECT archived_at, relevance_outcome FROM jobs WHERE job_id = 'linkedin:2'"
                ).fetchone()
            self.assertEqual(tuple(restored_state), (None, "manual_keep"))


class TestRelevanceAuditFilter(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "jobs.db"
        self.db = JobDatabase(path)
        settings = api.AppSettings(
            db_path=str(path), run_store_db_path=str(path), api_token="",
            max_log_bytes=30000, frontend_dist_dir=Path(tmp.name) / "dist",
        )
        settings_patch = patch.object(api, "APP_SETTINGS", settings)
        db_patch = patch.object(api, "job_db", self.db)
        settings_patch.start()
        db_patch.start()
        self.addCleanup(settings_patch.stop)
        self.addCleanup(db_patch.stop)

    def add_job(self, job_id: str, outcome: str | None, *, archived: bool = False,
                favorite: bool = False, source: str = "LinkedIn") -> None:
        with self.db._get_connection() as conn:  # noqa: SLF001
            conn.execute(
                """INSERT INTO jobs (job_id, title, company, location, source,
                       relevance_outcome, archived_at, is_favorite, scraped_at, first_seen_at, last_seen_at)
                   VALUES (?, ?, 'Acme', 'Berlin', ?, ?, ?, ?, '2026-09-04T08:00:00Z',
                           '2026-09-04T08:00:00Z', '2026-09-04T08:00:00Z')""",
                (job_id, job_id, source, outcome,
                 "2026-09-04T08:00:00Z" if archived else None, favorite),
            )
            conn.commit()

    def decision(self, job_id: str, source: str = "rule", action: str = "archive") -> None:
        self.db.record_filter_decisions([{
            "job_id": job_id, "decision_source": source, "decision_action": action,
            "filter_name": "audit_fixture", "reason": "Audit fixture",
            # Equal timestamps exercise decision-id ordering for successive decisions.
            "decided_at": "2026-09-04T08:00:00Z",
        }])

    def test_exact_outcomes_keep_archive_scope_and_unclassified_rows_separate(self) -> None:
        outcomes = ["unmatched", "target", "excluded", "unrelated", "manual_keep", "manual_archive"]
        for outcome in outcomes:
            self.add_job(outcome, outcome)
            self.add_job(f"archived-{outcome}", outcome, archived=True)
        self.add_job("legacy-unclassified", None)
        self.add_job("other-source", "unmatched", source="Indeed")

        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                active = _list_jobs(relevance_outcome=outcome)
                archived = _list_jobs(relevance_outcome=outcome, archived="only")
                all_jobs = _list_jobs(relevance_outcome=outcome, archived="include")
                self.assertEqual([j["job_id"] for j in active["items"]], [outcome])
                self.assertEqual([j["job_id"] for j in archived["items"]], [f"archived-{outcome}"])
                self.assertEqual(all_jobs["total"], 2)
        self.assertEqual(_list_jobs()["total"], len(outcomes) + 1)

    def test_automatic_archive_uses_current_decision_provenance_and_counts_canonical_jobs(self) -> None:
        for job_id, outcome in [("auto-excluded", "excluded"), ("auto-unrelated", "unrelated"),
                                ("auto-unmatched", "unmatched")]:
            self.add_job(job_id, outcome, archived=True)
            self.decision(job_id)
            self.decision(job_id)  # Repeated decisions must not duplicate list rows.
            self.decision(job_id, action="shadow")

        for job_id in ["manual", "legacy-ai", "legacy-unknown", "later-keep", "later-restore"]:
            self.add_job(job_id, "excluded", archived=True)
        self.decision("manual")
        self.decision("manual", source="manual")
        self.decision("legacy-ai", source="ai")
        for action in ["keep", "restore"]:
            self.decision(f"later-{action}")
            self.decision(f"later-{action}", action=action)
        self.add_job("manual-override", "manual_archive", archived=True)
        self.decision("manual-override")
        self.add_job("active", "unmatched")
        self.decision("active")
        self.add_job("other-source", "excluded", archived=True, source="Indeed")
        self.decision("other-source")

        # The audit selects archived jobs even when a caller leaves the default
        # active-only scope in place, and uses the same predicate for pagination.
        page = _list_jobs(relevance_outcome="auto_archived", limit=1, offset=1, sort="title_asc")
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["count"], 1)
        self.assertEqual(page["items"][0]["job_id"], "auto-unmatched")
        all_jobs = _list_jobs(relevance_outcome="auto_archived", archived="include")
        self.assertEqual({j["job_id"] for j in all_jobs["items"]},
                         {"auto-excluded", "auto-unrelated", "auto-unmatched"})

    def test_audit_combines_with_other_filters_and_restore_removes_the_job(self) -> None:
        self.add_job("matching", "excluded", archived=True, favorite=True)
        self.add_job("not-favorite", "excluded", archived=True)
        for job_id in ["matching", "not-favorite"]:
            self.decision(job_id)
        filters = dict(relevance_outcome="auto_archived", favorite=True, company="Acme",
                       location="Berlin", search="matching", date_from="2026-09-04",
                       date_to="2026-09-05")
        result = _list_jobs(**filters)
        self.assertEqual([j["job_id"] for j in result["items"]], ["matching"])
        self.assertEqual(_list_jobs(**{**filters, "company": "Missing"})["total"], 0)

        api.restore_job("matching", _=True)
        self.assertEqual(_list_jobs(**filters)["total"], 0)
        kept = _list_jobs(relevance_outcome="manual_keep", favorite=True)
        self.assertEqual([j["job_id"] for j in kept["items"]], ["matching"])
        self.assertIsNone(kept["items"][0]["archived_at"])


if __name__ == "__main__":
    unittest.main()
