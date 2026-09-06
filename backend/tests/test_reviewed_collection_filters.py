"""September 6 reviewed jobs, nearby title variants, and repeat collection."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from backend.src.config.settings import DEFAULT_UNWANTED_KEYWORDS
from backend.src.agents.job_scraper import JobScraper
from backend.src.descriptions.store import DescriptionStore
from backend.src.utils.database import JobDatabase
from backend.src.utils.relevance import classify_relevance
from backend.src.utils.relevance_service import apply_relevance_preview, build_relevance_preview


CASES = json.loads((Path(__file__).parent / 'fixtures' / 'reviewed_collection_20260906.json').read_text())['cases']


class TestReviewedCollectionFilters(unittest.TestCase):
    def classify(self, title, company='Example'):
        return classify_relevance(title, company=company,
                                  unwanted_keywords=DEFAULT_UNWANTED_KEYWORDS.split(','))

    def test_all_passed_jobs_have_the_reviewed_title_boundary(self):
        for case in CASES:
            with self.subTest(title=case['title'], job_id=case['job_id']):
                result = self.classify(case['title'], case['company'])
                self.assertEqual(result.outcome in {'excluded', 'unrelated'}, case['archive_by_title'])
                if not case['archive_by_title']:
                    # The 67 borderline/retained postings and the three generic
                    # data titles must not acquire a blanket title exclusion.
                    self.assertEqual(result.outcome, case['previous_outcome'])

    def test_spelling_gender_separators_and_role_variants(self):
        for title in [
            'SOC–Analyst', 'SOC\u00a0Analyst', 'SOC‑Analyst', 'SOC-Analyst',
            'IT-Projektassistenz', 'Junior IT-Anwendungsbetreuerin',
            'Mitarbeiter/in Logistik', 'Mitarbeiter*in Logistik',
            'Mitarbeiter:in Logistik', 'MitarbeiterIn Logistik',
            'Mitarbeiter(in) Logistik', 'Mitarbeiter/-in Logistik',
            'Mitarbeiterinnen in der Logistik', 'Mitarbeiter*in Sales & Operations',
            'Mitarbeiter/in Sales & Operations', 'Datenarchitektin',
            'CNC Maschinenbediener', 'CNC Maschienenbediener',
            'Front-Office-Agent', 'Night Auditor', 'Reservierungsmitarbeiterin',
            'Bar-Mitarbeiter', 'Counter Manager Cosmetics', 'Outlet Client Adviser',
            'POS-Promoter*in', 'Retention-Agent', 'Social Affiliate Specialist',
            'Inventory Manager', 'Global Manager Inventories',
            'Produktionsplanerin', 'Schichtleiterin', 'BIM-Modellierer:in',
            'Calculateur Construction', 'Export Control & Customs Expert',
            'Manager FP & A', 'Operational Risk Officer', 'Tax Analyst',
            'Portfolio Manager', 'SWIFT Specialist', 'KIS Administrator',
            'Patch-Management Expert', 'Geophysicist', 'Senior Meteorologist',
            'Spacecraft Analyst', 'QC-Analyst', 'Probenregistrierung (Labor)',
            'Quality Assurance & Sample Technician',
            'Full Time Banking Analyst (open to 2026 Summer Interns Only)',
        ]:
            with self.subTest(title=title):
                self.assertIn(self.classify(title).outcome, {'excluded', 'unrelated'})

    def test_adjacent_work_and_employers_are_not_blacklisted(self):
        for title in [
            'Data Scientist Logistics', 'Supply Chain Analyst',
            'Senior Product Analyst', 'Finance & BI Specialist',
            'Data Warehouse Engineer', 'Data Engineer / Analyst',
            'Data Quality Analyst', 'Data Analyst - Quality Assurance',
            'Data Scientist - Manufacturing', 'Data Scientist - Demand Modeling',
            'Data Scientist - AI Lab', 'Data Scientist - Physics-informed Machine Learning',
            'Geospatial Data Analyst - GIS', 'Senior Data Scientist',
            'Research Data Engineer', 'Data Scientist - Manager',
            "Founders' Associate", 'Performance Manager - Non contract',
            'Data Analyst supporting Summer Interns', 'Client Advisor',
        ]:
            with self.subTest(title=title):
                self.assertNotIn(self.classify(title).outcome, {'excluded', 'unrelated'})
        for company in ['Bucherer AG', 'Prada Group', 'Leibniz Institute', 'SAP', 'Haystack', 'Flix']:
            with self.subTest(company=company):
                self.assertEqual(self.classify('Data Scientist', company).outcome, 'target')
        self.assertEqual(self.classify('Client Advisor', 'Bucherer AG').outcome, 'unrelated')
        self.assertEqual(self.classify('Research Assistant in Knowledge Organisation', 'ZBW – Leibniz Information Centre').outcome, 'excluded')
        self.assertEqual(self.classify('Research Assistant', 'Industrial R&D').outcome, 'unmatched')

    def test_approved_archives_survive_repeat_collection_and_reconciliation(self):
        with TemporaryDirectory() as folder:
            db = JobDatabase(Path(folder) / 'jobs.db')
            frame = pd.DataFrame([dict(job_id=c['job_id'], title=c['title'], company=c['company'],
                                      location='Berlin', source='LinkedIn',
                                      url='https://www.linkedin.com/jobs/view/' + c['job_id'].split(':')[-1] + '/')
                                  for c in CASES])
            db.put_into_sql(frame, observed_at='2026-09-06T07:41:00Z')
            rejected = {c['job_id'] for c in CASES if c['reviewed_action'] == 'filter'}
            db.archive_jobs_with_filter_decisions([dict(job_id=job_id, decision_source='manual',
                decision_action='archive', filter_name='reviewed_september_6', reason='Reviewed exclusion')
                for job_id in rejected])
            db.put_into_sql(frame, observed_at='2026-09-07T07:41:00Z')
            preview = build_relevance_preview(db, active_only=False, reconcile_archive=True)
            apply_relevance_preview(db, preview)
            with db._get_connection() as conn:
                archived = {r[0] for r in conn.execute('SELECT job_id FROM jobs WHERE archived_at IS NOT NULL')}
                self.assertEqual(archived, rejected)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM job_observations').fetchone()[0], 262)
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_real_collection_pipeline_enforces_reviewed_rules_before_description_queueing(self):
        with TemporaryDirectory() as folder:
            db = JobDatabase(Path(folder) / 'jobs.db')
            scraper = JobScraper.__new__(JobScraper)
            scraper.db = db
            scraper.config = SimpleNamespace(
                search_keywords='Data Scientist,Data Analyst', search_locations='München',
                get_unwanted_keywords_list=lambda: DEFAULT_UNWANTED_KEYWORDS.split(','),
                get_unwanted_companies_list=lambda: [], relevance_mode='enforce', archive_unmatched_jobs=False,
            )
            scraper.collection_reports = []
            scraper.query_matches = []
            scraper.max_total_jobs = 0
            scraper.min_new_jobs_to_continue = 0
            frame = pd.DataFrame([dict(job_id=c['job_id'], title=c['title'], company=c['company'],
                source='LinkedIn', location='Munich, Bavaria, Germany',
                url='https://www.linkedin.com/jobs/view/' + c['job_id'].split(':')[-1] + '/') for c in CASES])
            scraper.scrape = Mock(return_value=frame)
            title_rejections = {c['job_id'] for c in CASES if c['archive_by_title']}
            approved_rejections = {c['job_id'] for c in CASES if c['reviewed_action'] == 'filter'}
            descriptions = DescriptionStore(str(db.db_path))
            with patch('backend.src.agents.job_scraper.update_api_run_progress'):
                self.assertTrue(scraper.execute_job_search())
                with db._get_connection() as conn:
                    self.assertEqual({r[0] for r in conn.execute('SELECT job_id FROM jobs WHERE archived_at IS NOT NULL')}, title_rejections)
                db.archive_jobs_with_filter_decisions([dict(job_id=job_id, decision_source='manual',
                    decision_action='archive', filter_name='reviewed_september_6', reason='Reviewed exclusion')
                    for job_id in approved_rejections])
                self.assertTrue(scraper.execute_job_search())
                with db._get_connection() as conn:
                    self.assertEqual({r[0] for r in conn.execute('SELECT job_id FROM jobs WHERE archived_at IS NOT NULL')}, approved_rejections)
                self.assertEqual(set(descriptions.unfetched_candidates()), set(frame.job_id) - approved_rejections)

                # Fresh posting IDs and a corrected city must not bypass the
                # title rules. Genuine DS/DA roles remain available.
                reposts = frame[frame.job_id.isin(title_rejections)].copy()
                reposts['job_id'] = [f'linkedin:{88000000 + i}' for i in range(len(reposts))]
                reposts['url'] = ['https://www.linkedin.com/jobs/view/' + job_id.split(':')[-1] + '/' for job_id in reposts.job_id]
                scraper.scrape.return_value = pd.concat([reposts, pd.DataFrame([
                    dict(job_id='linkedin:99000001',title='Senior Data Scientist',company='Example Science',source='LinkedIn',location='München'),
                    dict(job_id='linkedin:99000002',title='Data Analyst',company='Example Analytics',source='LinkedIn',location='München'),
                ])], ignore_index=True)
                self.assertTrue(scraper.execute_job_search())
                eligible = set(descriptions.unfetched_candidates())
                self.assertTrue(eligible.isdisjoint(set(reposts.job_id) | approved_rejections))
                self.assertTrue({'linkedin:99000001', 'linkedin:99000002'} <= eligible)
                with db._get_connection() as conn:
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM description_fetches').fetchone()[0], 0)
