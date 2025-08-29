"""
Task Scheduler Module
Handles manual execution of job scraping tasks
"""
import sys
import time
import pandas as pd
from loguru import logger
from ..config.settings import Config
from ..scrapers.job_scraper import JobScraper
from ..email_notifier.email_sender import EmailSender

class Utilities:
    """Handles manual execution of job scraping and notification tasks"""
    
    def __init__(self, config: Config):
        self.config = config
        self.scraper = JobScraper(config)
        self.email_sender = EmailSender(config)
        self._wait_tick_seconds = 1.0


    def _update_progress(self, completed: int, total: int, prefix: str = "") -> None:
        """Render a simple single-line progress bar in the terminal."""
        try:
            width = 30
            if total <= 0:
                bar = '-' * width
                line = f"\r{prefix}[{bar}] {completed}/{total}"
            else:
                filled = int(width * max(0, min(completed, total)) / total)
                bar = '█' * filled + '-' * (width - filled)
                line = f"\r{prefix}[{bar}] {completed}/{total}"
            sys.stdout.write(line)
            sys.stdout.flush()
            if total > 0 and completed >= total:
                sys.stdout.write("\n")
                sys.stdout.flush()
        except Exception:
            # Fallback silently if stdout is not available
            pass

    # This is the main scraper function. Gets other functions from job_scraper.py. Nutella
    def execute_job_search(self) -> bool:
        """Execute job search and send notifications"""
        
        try:
            # Parse keywords and locations from config
            keywords = [k.strip() for k in self.config.search_keywords.split(',')]
            locations = [l.strip() for l in self.config.search_locations.split(',')]
            jobs_df = self.scraper.scrape(keywords, locations)
            
            if not jobs_df.empty:
                # Store jobs in SQLite database (fast deduplication)
                new_jobs_count = self.scraper.db.put_into_sql(jobs_df)
                
                # Save CSV for backup/auditing unless dry-run
                csv_filename = None
                if not self.config.dry_run:
                    csv_filename = self.scraper.save_jobs_to_csv(jobs_df)
                else:
                    logger.info("DRY_RUN is enabled; skipping CSV save")
                
                # Send email with inline list of jobs (HTML + text), no attachment
                subject = f"Job Search Report - {new_jobs_count} new opportunities found!"
                if not self.config.dry_run and new_jobs_count > 0:
                    sent = self.email_sender.send_job_report(jobs_df, subject)
                    if not sent:
                        logger.warning("Email send returned False")
                elif self.config.dry_run:
                    logger.info("DRY_RUN is enabled; skipping email send")
                else:
                    logger.info("No new jobs detected; skipping email send")
                return True
            else:
                logger.warning("No jobs found in this search.")
                
                # Send notification about empty results (respect dry run)
                subject = "Job Search Report - No new opportunities found"
                if not self.config.dry_run:
                    self.email_sender.send_job_report(jobs_df, subject)
                else:
                    logger.info("DRY_RUN is enabled; skipping empty-results email")
                return False
                
        except Exception as e:
            logger.error(f"Error in job search: {e}")
            # Send error notification
            self.email_sender.send_error_notification(str(e))
            return False

    def get_search_summary(self) -> dict:
        """Get summary of search configuration"""
        return {
            'keywords': self.config.get_keywords_list(),
            'locations': self.config.get_locations_list(),
            'email_configured': bool(self.config.email_address and self.config.email_password),
            'recipient': self.config.recipient_email,
            'user_agent': self.config.user_agent[:50] + "..." if len(self.config.user_agent) > 50 else self.config.user_agent,
            'request_delay': self.config.request_delay,
            'max_retries': self.config.max_retries,
            'enable_linkedin': self.config.enable_linkedin,
            'dry_run': self.config.dry_run
        }
    
    def filter_and_send_historical_jobs(self) -> bool:
        """Apply current filters to historical data from database and send filtered results"""
        try:
            # Get all jobs from database and apply current filters
            with self.scraper.db._get_connection() as conn:
                all_jobs_df = pd.read_sql_query("SELECT * FROM jobs ORDER BY scraped_at DESC", conn)
            
            if all_jobs_df.empty:
                logger.warning("No historical job data found in database")
                subject = "Job Informer Filtered Historical Data - No Data Available"
                self.email_sender.send_job_report(all_jobs_df, subject)
                return False
            
            # Apply current filters (same logic as in scraper)
            filtered_jobs_df = self.scraper.filter_unwanted_jobs(all_jobs_df.copy())
            
            if filtered_jobs_df.empty:
                logger.warning("No jobs remained after filtering historical data")
                subject = "Job Informer Filtered Historical Data - No Jobs After Filtering"
                self.email_sender.send_job_report(filtered_jobs_df, subject)
                return False
            
            logger.info(f"Filtered historical data: {len(filtered_jobs_df)} jobs passed the current filters")
            
            # Send email with filtered historical jobs
            subject = f"Job Informer Filtered Historical Data - {len(filtered_jobs_df)} Jobs After Filtering"
            success = self.email_sender.send_job_report(filtered_jobs_df, subject)
            
            if not success:
                logger.error("Failed to send filtered historical data report")
                
            return success
            
        except Exception as e:
            logger.error(f"Error filtering historical jobs: {e}")
            self.email_sender.send_error_notification(str(e))
            return False

    def purge_unwanted_jobs(self) -> bool:
        """Purge unwanted jobs from the database based on current filters"""
        try:
            # Get all jobs from database
            with self.scraper.db._get_connection() as conn:
                all_jobs_df = pd.read_sql_query("SELECT * FROM jobs ORDER BY scraped_at DESC", conn)
            
            if all_jobs_df.empty:
                logger.warning("No jobs found in database to purge")
                return True
            
            original_count = len(all_jobs_df)
            logger.info(f"Found {original_count} jobs in database")

            # 1) Remove jobs matching unwanted keywords in TITLE only (substring, case-insensitive)
            import re as _re
            unwanted_kw = self.config.get_unwanted_keywords_list()
            if unwanted_kw and 'title' in all_jobs_df.columns:
                # Escape each keyword for regex, but do NOT use word boundaries so partials match (e.g., 'game' -> 'Gameplay')
                escaped_kw = [_re.escape(k) for k in unwanted_kw if k]
                if escaped_kw:
                    patt_kw = "|".join(escaped_kw)
                    mask_title_unwanted = all_jobs_df['title'].astype(str).str.contains(patt_kw, case=False, na=False, regex=True)
                    unwanted_by_title = all_jobs_df[mask_title_unwanted]
                else:
                    unwanted_by_title = all_jobs_df.iloc[0:0]
            else:
                unwanted_by_title = all_jobs_df.iloc[0:0]

            # Unwanted by company names (COMPANY only, case-insensitive substring)
            unwanted_companies = self.config.get_unwanted_companies_list()
            if unwanted_companies and 'company' in all_jobs_df.columns:
                terms = [_re.escape(c) for c in unwanted_companies if c]
                if terms:
                    patt_co = "|".join(terms)
                    mask_company_unwanted = all_jobs_df['company'].astype(str).str.contains(patt_co, case=False, na=False, regex=True)
                    unwanted_by_company = all_jobs_df[mask_company_unwanted]
                else:
                    unwanted_by_company = all_jobs_df.iloc[0:0]
            else:
                unwanted_by_company = all_jobs_df.iloc[0:0]

            # 1.5) Remove jobs with part-time employment type from parsed descriptions
            import json
            part_time_job_ids = set()
            try:
                with self.scraper.db._get_connection() as conn:
                    part_time_df = pd.read_sql_query("""
                        SELECT DISTINCT j.job_id, j.title, j.company, p.payload_json
                        FROM jobs j
                        INNER JOIN parsed_descriptions p ON j.job_id = p.job_id
                        INNER JOIN (
                            SELECT job_id, MAX(version) as max_version
                            FROM parsed_descriptions
                            GROUP BY job_id
                        ) latest ON p.job_id = latest.job_id AND p.version = latest.max_version
                    """, conn)
                
                part_time_examples = []
                for _, row in part_time_df.iterrows():
                    try:
                        payload = json.loads(row['payload_json'])
                        employment_type = payload.get('employment_type', '').lower()
                        
                        # Concise combined check for part-time/contract/internship employment types
                        tokens = ('part-time', 'contract', 'internship')
                        if any(t in employment_type for t in tokens):
                            part_time_job_ids.add(row['job_id'])
                            if len(part_time_examples) < 3:
                                part_time_examples.append((row['title'], row['company']))

                    except (json.JSONDecodeError, KeyError):
                        continue
                
                if part_time_job_ids:
                    logger.info(f"Found {len(part_time_job_ids)} part-time jobs to remove based on parsed descriptions")
                    if part_time_examples:
                        logger.info("Examples of part-time jobs:")
                        for title, company in part_time_examples:
                            logger.info(f"  - {title} at {company}")
                            
            except Exception as e:
                logger.warning(f"Could not check part-time jobs from parsed descriptions: {e}")

            # Combine unwanted ids
            unwanted_ids = set()
            if not unwanted_by_title.empty:
                unwanted_ids.update(unwanted_by_title['job_id'].tolist())
            if not unwanted_by_company.empty:
                unwanted_ids.update(unwanted_by_company['job_id'].tolist())
            if part_time_job_ids:
                unwanted_ids.update(part_time_job_ids)

            # 2) Remove duplicates: keep most recent per normalized (title, company, source)
            #    Note: We intentionally ignore location so entries with the same title+company
            #    but different locations are considered duplicates and purged.
            norm_df = all_jobs_df.copy()
            for col in ['title', 'company', 'location', 'source']:
                if col in norm_df.columns:
                    norm_df[f'{col}_norm'] = norm_df[col].astype(str).str.strip().str.lower()
                else:
                    norm_df[f'{col}_norm'] = ''
            # Parse scraped_at for recency sort
            if 'scraped_at' in norm_df.columns:
                try:
                    norm_df['scraped_at'] = pd.to_datetime(norm_df['scraped_at'], errors='coerce')
                except Exception:
                    pass
            norm_df['_order'] = norm_df['scraped_at']
            try:
                norm_df['_order'] = norm_df['_order'].fillna(pd.Timestamp(0))
            except Exception:
                pass

            norm_df_sorted = norm_df.sort_values(by=['_order'], ascending=False)
            keep_idx = norm_df_sorted.drop_duplicates(
                subset=['title_norm', 'company_norm', 'source_norm'], keep='first'
            ).index
            dup_mask = ~norm_df.index.isin(keep_idx)
            duplicate_jobs = all_jobs_df[dup_mask]
            duplicate_ids = set(duplicate_jobs['job_id'].tolist())

            # Union of all job_ids to delete
            to_delete_ids = list(unwanted_ids.union(duplicate_ids))

            if not to_delete_ids:
                logger.info("No unwanted or duplicate jobs found - database is clean")
                return True

            logger.info(
                f"Will remove {len(to_delete_ids)} jobs (unwanted: {len(unwanted_ids)} [keywords: {len(unwanted_by_title)}, companies: {len(unwanted_by_company)}, part-time: {len(part_time_job_ids)}], duplicates: {len(duplicate_ids)})"
            )

            # Delete selected jobs
            with self.scraper.db._get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.executemany("DELETE FROM jobs WHERE job_id = ?", [(jid,) for jid in to_delete_ids])
                    conn.commit()
                    
                    # Also clean up orphaned parsed descriptions for deleted jobs
                    if to_delete_ids:
                        cursor.executemany("DELETE FROM parsed_descriptions WHERE job_id = ?", [(jid,) for jid in to_delete_ids])
                        conn.commit()
                except Exception as e:
                    logger.error(f"Error during deletion: {e}")
                    return False

                # Verify deletion
                cursor.execute("SELECT COUNT(*) FROM jobs")
                final_count = cursor.fetchone()[0]

            logger.info(
                f"Purge complete: {len(to_delete_ids)} jobs removed (unwanted: {len(unwanted_ids)} [keywords: {len(unwanted_by_title)}, companies: {len(unwanted_by_company)}, part-time: {len(part_time_job_ids)}], duplicates: {len(duplicate_ids)}). Remaining: {final_count}"
            )

            if len(to_delete_ids) > 0:
                # Show examples
                frames = []
                if 'unwanted_by_title' in locals() and not unwanted_by_title.empty:
                    frames.append(unwanted_by_title)
                if 'unwanted_by_company' in locals() and not unwanted_by_company.empty:
                    frames.append(unwanted_by_company)
                frames.append(duplicate_jobs)
                sample_display = pd.concat(frames, ignore_index=True).head(3)
                if not sample_display.empty:
                    logger.info("Examples of removed jobs:")
                    for _, job in sample_display.iterrows():
                        logger.info(f"  - {job['title']} at {job['company']}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error purging unwanted jobs: {e}")
            return False


