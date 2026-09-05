"""Read-only preview, transactional application, and manual-override tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from backend.src.agents.job_scraper import JobScraper
from backend.src.config.settings import DEFAULT_UNWANTED_KEYWORDS
from backend.src.utils.database import JobDatabase
from backend.src.utils.relevance_service import apply_relevance_preview, build_relevance_preview


def _jobs() -> pd.DataFrame:
    titles = [
        "Data Analyst",
        "Disponent:in LKW-Transporte",
        "Mystery Specialist",
        "AI Engineer",
        "Teamassistent",
    ]
    return pd.DataFrame(
        [
            {
                "job_id": f"linkedin:{index}",
                "title": title,
                "company": "ACME",
                "location": "Berlin",
                "source": "LinkedIn",
                "url": f"https://www.linkedin.com/jobs/view/{index}/",
            }
            for index, title in enumerate(titles, start=1)
        ]
    )


class TestRelevanceWorkflow(unittest.TestCase):
    def test_collection_enforces_scope_exclusions_not_covered_by_legacy_filters(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            scraper = JobScraper.__new__(JobScraper)
            scraper.db = db
            scraper.last_run_failed = False
            scraper.collection_reports = []
            scraper.query_matches = []
            scraper.max_total_jobs = 0
            scraper.min_new_jobs_to_continue = 0
            scraper.config = SimpleNamespace(
                search_keywords="Data Scientist",
                search_locations="Berlin",
                get_unwanted_keywords_list=lambda: DEFAULT_UNWANTED_KEYWORDS.split(","),
                get_unwanted_companies_list=lambda: [],
                relevance_mode="enforce",
                archive_unmatched_jobs=False,
            )
            observed = pd.DataFrame(
                [
                    {
                        "job_id": "linkedin:1001",
                        "title": "Backend Engineer",
                        "company": "ACME",
                        "location": "Berlin",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/1001/",
                    },
                    {
                        "job_id": "linkedin:1002",
                        "title": "Business Analyst",
                        "company": "ACME",
                        "location": "Berlin",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/1002/",
                    },
                    {
                        "job_id": "linkedin:1003",
                        "title": "Senior Data Analyst SAP",
                        "company": "ACME",
                        "location": "Berlin",
                        "source": "LinkedIn",
                        "url": "https://www.linkedin.com/jobs/view/1003/",
                    },
                ]
            )

            with patch.object(scraper, "scrape", return_value=observed), patch(
                "backend.src.agents.job_scraper.update_api_run_progress"
            ):
                self.assertTrue(scraper.execute_job_search())

            with db._get_connection() as conn:  # noqa: SLF001
                states = {
                    row[0]: (row[1], row[2])
                    for row in conn.execute(
                        "SELECT job_id, relevance_outcome, archived_at FROM jobs"
                    )
                }
                scope_decisions = conn.execute(
                    """
                    SELECT COUNT(*) FROM filter_decisions
                    WHERE job_id = 'linkedin:1001'
                      AND filter_name = 'scope_exclusion'
                      AND decision_action = 'archive'
                    """
                ).fetchone()[0]

            self.assertEqual(states["linkedin:1001"][0], "excluded")
            self.assertIsNotNone(states["linkedin:1001"][1])
            self.assertEqual(states["linkedin:1002"][0], "unmatched")
            self.assertIsNone(states["linkedin:1002"][1])
            self.assertEqual(states["linkedin:1003"][0], "excluded")
            self.assertIsNotNone(states["linkedin:1003"][1])
            self.assertEqual(scope_decisions, 1)

    def test_validation_labels_are_sql_only_and_do_not_change_job_state(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            db.put_into_sql(_jobs(), observed_at="2026-09-04T08:00:00Z")

            saved = db.save_relevance_validation_labels(
                [
                    {
                        "job_id": "linkedin:1",
                        "label": "target",
                        "notes": "Explicit data-analytics title",
                        "policy_version": "2026-09-04.3",
                    },
                    {
                        "job_id": "linkedin:2",
                        "label": "noise",
                        "notes": "Operational logistics",
                        "policy_version": "2026-09-04.3",
                    },
                    {
                        "job_id": "linkedin:3",
                        "label": "adjacent",
                        "notes": "Retained for discovery",
                        "policy_version": "2026-09-04.3",
                    },
                ]
            )
            self.assertEqual(saved, 3)

            with db._get_connection() as conn:  # noqa: SLF001
                labels = dict(
                    conn.execute(
                        "SELECT job_id, label FROM relevance_validation_labels"
                    ).fetchall()
                )
                active_count = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE archived_at IS NULL"
                ).fetchone()[0]
            self.assertEqual(
                labels,
                {
                    "linkedin:1": "target",
                    "linkedin:2": "noise",
                    "linkedin:3": "adjacent",
                },
            )
            self.assertEqual(active_count, 5)

    def test_preview_is_read_only_and_apply_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            db.put_into_sql(_jobs(), observed_at="2026-09-04T08:00:00Z")
            db.record_filter_decisions(
                [{
                    "job_id": "linkedin:5",
                    "decision_source": "manual",
                    "decision_action": "keep",
                    "filter_name": "manual_keep",
                    "reason": "Operator override",
                }]
            )

            preview = build_relevance_preview(db)
            self.assertEqual(preview["counts"], {
                "manual_keep": 1,
                "excluded": 1,
                "unmatched": 1,
                "unrelated": 1,
                "target": 1,
            })
            self.assertEqual(preview["would_archive"], 2)
            self.assertEqual(len(preview["manual_overrides"]), 1)

            with db._get_connection() as conn:  # noqa: SLF001
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM filter_decisions").fetchone()[0], 1)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM jobs WHERE relevance_outcome IS NOT NULL").fetchone()[0],
                    0,
                )

            first = apply_relevance_preview(db, preview)
            second = apply_relevance_preview(db, preview)
            self.assertEqual(first["archived"], 2)
            self.assertEqual(first["decisions_recorded"], 4)
            self.assertEqual(second["archived"], 0)
            self.assertEqual(second["decisions_recorded"], 0)

            with db._get_connection() as conn:  # noqa: SLF001
                states = {
                    row[0]: (row[1], row[2], row[3])
                    for row in conn.execute(
                        "SELECT job_id, relevance_outcome, role_family, archived_at FROM jobs"
                    )
                }
                decision_count = conn.execute("SELECT COUNT(*) FROM filter_decisions").fetchone()[0]
            self.assertEqual(states["linkedin:1"][:2], ("target", "analytics_bi"))
            self.assertIsNone(states["linkedin:1"][2])
            self.assertIsNotNone(states["linkedin:2"][2])
            self.assertIsNone(states["linkedin:3"][2])
            self.assertIsNotNone(states["linkedin:4"][2])
            self.assertEqual(states["linkedin:5"][0], "manual_keep")
            self.assertIsNone(states["linkedin:5"][2])
            self.assertEqual(decision_count, 5)

    def test_manual_restore_survives_a_later_collection(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            job = _jobs().iloc[[1]].copy()
            db.put_into_sql(job, observed_at="2026-09-04T08:00:00Z")
            db.archive_jobs(["linkedin:2"], archived_reason="Initial rule")
            self.assertTrue(db.restore_job("linkedin:2"))

            db.put_into_sql(job, observed_at="2026-09-05T08:00:00Z")
            preview = build_relevance_preview(db)
            self.assertEqual(preview["counts"], {"manual_keep": 1})
            apply_relevance_preview(db, preview)
            with db._get_connection() as conn:  # noqa: SLF001
                archived_at = conn.execute(
                    "SELECT archived_at FROM jobs WHERE job_id = 'linkedin:2'"
                ).fetchone()[0]
            self.assertIsNone(archived_at)

    def test_full_reconciliation_restores_old_automatic_archives_but_preserves_manual_and_duplicate_archives(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db = JobDatabase(Path(tmp_dir) / "jobs.db")
            jobs = pd.DataFrame(
                [
                    {
                        "job_id": f"linkedin:{index}",
                        "title": title,
                        "company": "ACME",
                        "location": "Berlin",
                        "source": "LinkedIn",
                        "url": f"https://www.linkedin.com/jobs/view/{index}/",
                    }
                    for index, title in enumerate(
                        [
                            "Data Scientist",
                            "Business Analyst",
                            "Data Analyst",
                            "Data Scientist",
                            "Backend Engineer",
                        ],
                        start=10,
                    )
                ]
            )
            db.put_into_sql(jobs, observed_at="2026-09-04T08:00:00Z")
            db.archive_jobs(["linkedin:10", "linkedin:11"], archived_reason="Old automatic filter")
            db.archive_jobs(["linkedin:12"], archived_reason="Duplicate posting")
            db.archive_jobs_with_filter_decisions(
                [{
                    "job_id": "linkedin:13",
                    "decision_source": "manual",
                    "decision_action": "archive",
                    "filter_name": "manual_archive",
                    "reason": "Archived manually",
                }]
            )

            preview = build_relevance_preview(
                db,
                active_only=False,
                reconcile_archive=True,
            )
            self.assertEqual(preview["would_restore"], 2)
            self.assertEqual(preview["would_archive"], 1)
            self.assertEqual(preview["final_active"], 2)

            result = apply_relevance_preview(db, preview)
            self.assertEqual(result["restored"], 2)
            self.assertEqual(result["archived"], 1)
            with db._get_connection() as conn:  # noqa: SLF001
                states = {
                    row[0]: row[1]
                    for row in conn.execute("SELECT job_id, archived_at FROM jobs")
                }
                filtered_ids = {
                    row[0] for row in conn.execute("SELECT job_id FROM filtered_jobs")
                }
            self.assertIsNone(states["linkedin:10"])
            self.assertIsNone(states["linkedin:11"])
            self.assertIsNotNone(states["linkedin:12"])
            self.assertIsNotNone(states["linkedin:13"])
            self.assertIsNotNone(states["linkedin:14"])
            self.assertEqual(filtered_ids, {"linkedin:10", "linkedin:11"})


if __name__ == "__main__":
    unittest.main()
