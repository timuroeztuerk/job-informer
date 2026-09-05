"""Market snapshot counts and canonical drill-downs must describe the same jobs."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend import api
from backend.src.utils.database import JobDatabase
from backend.src.utils.db_summary import build_db_summary
from backend.tests.test_api_relevance import _list_jobs

AS_OF = '2026-09-05T12:00:00Z'


class TestIntelligenceSummary(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'jobs.db'
        self.db = JobDatabase(self.path)
        settings = api.AppSettings(db_path=str(self.path), run_store_db_path=str(self.path),
                                   api_token='', max_log_bytes=30000, frontend_dist_dir=Path(tmp.name) / 'dist')
        settings_patch = patch.object(api, 'APP_SETTINGS', settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

    def seed(self):
        rows = [
            ('repeat', 'ACME', 'Munich, Bavaria, Germany', '2026-07-01T12:00:00Z', '2026-09-04T12:00:00Z', 3, 'data_science', 'target', False, False, 'LinkedIn'),
            ('new', 'ACME', 'München, Bayern, Deutschland', '2026-09-01T12:00:00Z', '2026-09-04T12:00:00Z', 1, 'analytics_bi', 'target', False, False, 'LinkedIn'),
            ('adjacent', 'ACME Tools', 'München', '2026-09-04T12:00:00Z', '2026-09-04T12:00:00Z', 2, None, 'unmatched', False, True, 'LinkedIn'),
            ('old', 'OldCo', 'Berlin', '2026-07-01T12:00:00Z', '2026-08-15T12:00:00Z', 4, 'data_science', 'target', False, False, 'LinkedIn'),
            ('boundary', 'Acme', 'Berlin', '2026-07-01T12:00:00Z', '2026-08-29T14:00:00+02:00', 1, 'data_science', 'target', False, False, 'LinkedIn'),
            ('auto', 'ACME', 'Munich', '2026-09-03T12:00:00Z', '2026-09-03T12:00:00Z', 1, None, 'excluded', True, False, 'LinkedIn'),
            ('manual', 'ACME', 'Munich', '2026-09-03T12:00:00Z', '2026-09-03T12:00:00Z', 1, None, 'manual_archive', True, False, 'LinkedIn'),
            ('other-source', 'ACME', 'Munich', '2026-09-03T12:00:00Z', '2026-09-03T12:00:00Z', 9, None, 'unmatched', False, True, 'Indeed'),
        ]
        with self.db._get_connection() as conn:
            for job_id, company, location, first, last, seen, family, outcome, archived, favorite, source in rows:
                conn.execute('''INSERT INTO jobs (job_id,title,company,location,source,scraped_at,
                    first_seen_at,last_seen_at,seen_count,role_family,relevance_outcome,archived_at,is_favorite)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (job_id, job_id, company, location, source, first, first, last, seen, family, outcome,
                     last if archived else None, favorite))
        for job_id, source in [('auto', 'rule'), ('manual', 'manual')]:
            self.db.record_filter_decisions([{'job_id': job_id, 'decision_source': source,
                'decision_action': 'archive', 'filter_name': 'fixture', 'reason': 'Fixture decision'}])
        for index in range(2):
            run = self.db.start_scrape_run(keywords=['Data Scientist'], locations=['Germany'], observed_at=AS_OF)
            self.db.record_collection_queries(run, [{'keyword': 'Data Scientist', 'location': 'Germany',
                'query_group_key': 'data_science', 'query_group_name': 'Data science'}])
            self.db.record_job_query_matches(run, [{'job_id': 'repeat', 'query_text': 'Data Scientist', 'query_location': 'Germany'}])

    def test_recent_window_uses_last_sighting_and_preserves_new_vs_repeat_distinction(self):
        self.seed()
        summary = build_db_summary(self.db, window='7d', as_of=AS_OF)
        self.assertEqual(summary['totals']['total_jobs'], 4)
        self.assertEqual(summary['scope']['all_active_jobs'], 5)
        self.assertEqual(summary['totals']['recent_jobs_7_days'], 2)
        self.assertEqual(summary['totals']['repeated_jobs'], 2)
        self.assertEqual(summary['totals']['companies'], 3)
        self.assertEqual(summary['totals']['locations'], 2)
        self.assertEqual(summary['review'], {'unmatched_jobs': 1, 'automatic_archives': 1, 'favorite_jobs': 1})
        self.assertEqual([j['job_id'] for j in summary['recurring_jobs']], ['repeat', 'adjacent'])
        self.assertIs(summary['recurring_jobs'][1]['is_favorite'], True)
        self.assertEqual(sum(r['count'] for r in summary['role_families']), 4)
        self.assertEqual(summary['query_groups'][0]['key'], 'data_science')
        self.assertEqual(summary['query_groups'][0]['name'], 'Data science')
        self.assertEqual(summary['query_groups'][0]['count'], 1)
        self.assertEqual(build_db_summary(self.db, window='30d', as_of=AS_OF)['totals']['total_jobs'], 5)
        self.assertEqual(build_db_summary(self.db, as_of=AS_OF)['totals']['total_jobs'], 5)

    def test_every_market_drill_down_matches_its_snapshot_count(self):
        self.seed()
        summary = build_db_summary(self.db, window='7d', as_of=AS_OF)
        base = {'last_seen_from': summary['scope']['seen_since']}
        for item in summary['top_companies']:
            with self.subTest(company=item['key']):
                self.assertEqual(_list_jobs(**base, company=item['key'], company_exact=True)['total'], item['count'])
        for item in summary['top_locations']:
            with self.subTest(location=item['key']):
                self.assertEqual(_list_jobs(**base, location=item['key'], location_primary=True)['total'], item['count'])
        for item in summary['role_families']:
            self.assertEqual(_list_jobs(**base, role_family=item['key'])['total'], item['count'])
        for item in summary['query_groups']:
            self.assertEqual(_list_jobs(**base, query_group=item['key'])['total'], item['count'])
        self.assertEqual(_list_jobs(**base, repeated=True)['total'], summary['totals']['repeated_jobs'])
        self.assertEqual(_list_jobs(**base, date_from=summary['scope']['recent_since'])['total'], summary['totals']['recent_jobs_7_days'])
        self.assertEqual(_list_jobs(**base, relevance_outcome='auto_archived')['total'], summary['review']['automatic_archives'])
        self.assertEqual(_list_jobs(**base, favorite=True)['total'], summary['review']['favorite_jobs'])
        # A partial-name search remains broad outside an exact Intelligence link.
        self.assertEqual(_list_jobs(**base, company='ACME')['total'], 4)

    def test_empty_or_stale_snapshot_has_zero_counts_without_losing_the_archive(self):
        empty = build_db_summary(self.db, window='7d', as_of=AS_OF)
        self.assertEqual(empty['totals']['total_jobs'], 0)
        self.assertEqual(empty['recurring_jobs'], [])
        self.assertEqual(empty['top_locations'], [])
        self.assertEqual(empty['review']['automatic_archives'], 0)
        self.seed()
        stale = build_db_summary(self.db, window='7d', as_of='2026-12-01T12:00:00Z')
        self.assertEqual(stale['totals']['total_jobs'], 0)
        self.assertEqual(stale['scope']['all_active_jobs'], 5)
        self.assertEqual(stale['collection_freshness']['status'], 'stale')

    def test_fractional_second_boundary_matches_the_list_date_filter(self):
        self.seed()
        with self.db._get_connection() as conn:
            conn.execute("UPDATE jobs SET scraped_at='2026-08-29T12:00:00.100000Z' WHERE job_id='new'")
        summary = build_db_summary(self.db, window='7d', as_of='2026-09-05T12:00:00.900000Z')
        results = _list_jobs(last_seen_from=summary['scope']['seen_since'], date_from=summary['scope']['recent_since'])
        self.assertEqual(summary['totals']['recent_jobs_7_days'], 2)
        self.assertEqual(results['total'], 2)


if __name__ == '__main__':
    unittest.main()
