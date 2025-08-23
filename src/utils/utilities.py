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
from ..description_parser.parser import DescriptionParser


class Utilities:
    """Handles manual execution of job scraping and notification tasks"""
    
    def __init__(self, config: Config):
        self.config = config
        self.scraper = JobScraper(config)
        self.email_sender = EmailSender(config)
        self.desc_parser = DescriptionParser(config, self.scraper.db)
        self._wait_tick_seconds = 1.0

    def _log_wait(self, message: str) -> None:
        try:
            logger.info(message)
        except Exception:
            pass

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

    def _print_city_and_description_summary(self, jobs_df: pd.DataFrame, title: str = "Run Summary") -> None:
        """Pretty-print a city breakdown and description coverage for the provided jobs."""
        if jobs_df is None or jobs_df.empty:
            print(f"\n==== {title} ====\nNo jobs to summarize.\n")
            return

        df = jobs_df.copy()
        # Ensure columns exist
        if 'location' not in df.columns:
            df['location'] = ''
        if 'description' not in df.columns:
            df['description'] = ''

        # Derive city from location (first token before comma)
        df['city'] = (
            df['location'].astype(str).str.split(',').str[0].str.strip().replace({'': 'Unknown'})
        )
        # Non-empty description
        desc_series = df['description'].astype(str).fillna('').str.strip()
        df['_has_desc'] = desc_series.str.len() > 0

        total = len(df)
        with_desc = int(df['_has_desc'].sum())
        pct_desc = (with_desc / total * 100.0) if total else 0.0

        print(f"\n==== {title} ====")
        print(f"Total jobs: {total}")
        print(f"With descriptions: {with_desc} ({pct_desc:.1f}%)")

        # City breakdown (top 10 by total)
        grouped = df.groupby('city', as_index=False).agg(
            total=('city', 'size'),
            with_desc=('_has_desc', 'sum')
        )
        grouped = grouped.assign(
            pct_desc=grouped.apply(
                lambda r: (float(r['with_desc']) / float(r['total']) * 100.0) if r['total'] else 0.0,
                axis=1,
            )
        )
        # Use stable two-pass sort to avoid type-checker mismatch and ensure proper ordering
        grouped = grouped.sort_values(by='with_desc', ascending=False, kind='mergesort')
        grouped = grouped.sort_values(by='total', ascending=False, kind='mergesort')
        grouped = grouped.head(10)

        if not grouped.empty:
            # Pretty table widths
            col_city, col_total, col_with, col_pct = 'City', 'Total', 'With Desc', '% Desc'
            w_city = max(len(col_city), *(len(str(c)) for c in grouped['city'].tolist()))
            w_total = max(len(col_total), *(len(str(x)) for x in grouped['total'].tolist()))
            w_with = max(len(col_with), *(len(str(x)) for x in grouped['with_desc'].tolist()))
            w_pct = max(len(col_pct), 6)
            header = f"{col_city:<{w_city}} | {col_total:>{w_total}} | {col_with:>{w_with}} | {col_pct:>{w_pct}}"
            sep = '-' * len(header)
            print("\nTop cities (by total jobs):")
            print(header)
            print(sep)
            for _, row in grouped.iterrows():
                city = str(row['city'])
                tot = int(row['total'])
                wd = int(row['with_desc'])
                pct = f"{(row['pct_desc']):.1f}"
                print(f"{city:<{w_city}} | {tot:>{w_total}} | {wd:>{w_with}} | {pct:>{w_pct}}")
        print("")
        
    def execute_job_search(self) -> bool:
        """Execute job search and send notifications - manual run"""
        logger.info("Starting manual job search...")
        
        try:
            # Parse keywords and locations from config
            keywords = [k.strip() for k in self.config.search_keywords.split(',')]
            locations = [l.strip() for l in self.config.search_locations.split(',')]
            
            # Scrape jobs from all enabled sources
            jobs_df = self.scraper.scrape_all_sources(keywords, locations)
            
            if not jobs_df.empty:
                # Terminal summary: city breakdown + description coverage
                self._print_city_and_description_summary(jobs_df)
                # Store jobs in SQLite database (fast deduplication)
                new_jobs_count = self.scraper.db.upsert_jobs(jobs_df)
                
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
                
                logger.success(f"Job search completed successfully. Found {new_jobs_count} new jobs.")
                if csv_filename:
                    logger.info(f"Results archived to: {csv_filename}")
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

    def run_dry_run(self) -> bool:
        """Run a fast dry run: scrape minimally and do not persist or email."""
        try:
            if not self.config.dry_run:
                logger.info("DRY_RUN is disabled; skipping dry-run phase")
                return True

            logger.info("=== Starting Fast Dry Run ===")
            # Derive limited scope
            keywords = self.config.get_keywords_list()[: max(1, int(self.config.dry_run_keywords_limit))]
            locations = self.config.get_locations_list()[: max(1, int(self.config.dry_run_locations_limit))]

            # Temporarily clamp scraping-related knobs
            original_request_delay = self.config.request_delay
            original_max_retries = self.config.max_retries
            original_linkedin_pages = getattr(self.config, 'linkedin_max_pages', 4)

            if getattr(self.config, 'dry_run_fast', True):
                self.config.request_delay = min(self.config.request_delay, float(self.config.dry_run_request_delay))
                self.config.max_retries = min(self.config.max_retries, int(self.config.dry_run_max_retries))
                self.config.linkedin_max_pages = int(self.config.dry_run_pages)
                # description fetching disabled in main scraper; no desc limit needed

            # Execute scrape with caps and skip selenium if asked
            jobs_df = self.scraper.scrape_all_sources(
                keywords,
                locations,
                limit_per_source=max(0, int(self.config.dry_run_limit_per_source)),
            )

            # Restore config values
            self.config.request_delay = original_request_delay
            self.config.max_retries = original_max_retries
            self.config.linkedin_max_pages = original_linkedin_pages
            # no description-related restoration needed

            # Print summary only, do not persist or email
            self._print_city_and_description_summary(jobs_df, title="Dry Run Summary")
            if jobs_df.empty:
                logger.warning("Dry run produced no jobs (this is ok for quick checks)")
            else:
                logger.info(f"Dry run collected {len(jobs_df)} jobs (not saved/email)")
            logger.success("=== Fast Dry Run Completed ===")
            return True
        except Exception as e:
            logger.error(f"Dry run failed: {e}")
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
            # Indeed removed
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

    def run_description_parser(self) -> bool:
        """Run the incremental description parser with caching, controlled via config knobs."""
        try:
            if not getattr(self.config, 'enable_description_parser', False):
                logger.info("ENABLE_DESCRIPTION_PARSER is disabled; skipping parsing")
                return True
            batch_size = int(getattr(self.config, 'desc_parser_batch_size', 25))
            max_batches = int(getattr(self.config, 'desc_parser_max_batches', 4))
            logger.info(f"Running description parser: batch_size={batch_size}, max_batches={max_batches}, version={getattr(self.config, 'desc_parser_version', 1)}")
            new_items = self.desc_parser.run_incremental(batch_size=batch_size, max_batches=max_batches)
            logger.info(f"Description parser stored {new_items} new parsed payloads")
            return True
        except Exception as e:
            logger.error(f"Description parser error: {e}")
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

            # Combine unwanted ids
            unwanted_ids = set()
            if not unwanted_by_title.empty:
                unwanted_ids.update(unwanted_by_title['job_id'].tolist())
            if not unwanted_by_company.empty:
                unwanted_ids.update(unwanted_by_company['job_id'].tolist())

            # 2) Remove duplicates: keep most recent per normalized (title, company, location, source)
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
                subset=['title_norm', 'company_norm', 'location_norm', 'source_norm'], keep='first'
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
                f"Will remove {len(to_delete_ids)} jobs (unwanted: {len(unwanted_ids)}, duplicates: {len(duplicate_ids)})"
            )

            # Delete selected jobs
            with self.scraper.db._get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.executemany("DELETE FROM jobs WHERE job_id = ?", [(jid,) for jid in to_delete_ids])
                    conn.commit()
                except Exception as e:
                    logger.error(f"Error during deletion: {e}")
                    return False

                # Verify deletion
                cursor.execute("SELECT COUNT(*) FROM jobs")
                final_count = cursor.fetchone()[0]

            logger.info(
                f"Purge complete: {len(to_delete_ids)} jobs removed (unwanted: {len(unwanted_ids)}, duplicates: {len(duplicate_ids)}). Remaining: {final_count}"
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

    def backfill_missing_descriptions(self, batch_size: int = 50, max_batches: int = 10) -> bool:
        """Fetch and fill descriptions for jobs in DB missing descriptions.
        Processes up to (batch_size * max_batches) jobs with gentle pacing and rate-limit awareness.
        Jobs that fail to fetch descriptions are marked to avoid retrying.
        """
        try:
            total_updated = 0
            total_failed = 0
            batches_processed = 0
            consecutive_empty_batches = 0
            
            while batches_processed < max_batches:
                self._log_wait(f"Querying DB for up to {batch_size} jobs missing descriptions")
                to_fill = self.scraper.db.get_jobs_missing_descriptions(limit=batch_size, exclude_failed=True)
                if to_fill.empty:
                    consecutive_empty_batches += 1
                    if consecutive_empty_batches >= 2:
                        logger.info("No more jobs with missing descriptions found (2 consecutive empty batches).")
                        break
                    logger.info("No jobs with missing descriptions found in this batch.")
                    time.sleep(1.0)  # Brief pause before checking again
                    continue
                else:
                    consecutive_empty_batches = 0

                logger.info(f"Backfill batch {batches_processed+1}: processing {len(to_fill)} jobs without descriptions")

                updates = []
                failed_job_ids = []
                total_in_batch = int(len(to_fill))
                completed_in_batch = 0
                self._update_progress(0, total_in_batch, prefix=f"Backfill batch {batches_processed+1}: ")
                
                for _, row in to_fill.iterrows():
                    url = str(row.get('url') or '')
                    source = str(row.get('source') or '')
                    job_id = str(row.get('job_id'))
                    
                    if not url:
                        failed_job_ids.append(job_id)  # No URL to fetch from
                        completed_in_batch += 1
                        self._update_progress(completed_in_batch, total_in_batch, prefix=f"Backfill batch {batches_processed+1}: ")
                        continue
                        
                    desc = self.scraper.fetch_job_description(url, source)
                    if desc and desc.strip():
                        updates.append({'job_id': job_id, 'description': desc})
                    else:
                        failed_job_ids.append(job_id)  # Failed to fetch or empty description
                        
                    # gentle pacing between requests
                    time.sleep(max(0.5, self.config.request_delay))
                    completed_in_batch += 1
                    self._update_progress(completed_in_batch, total_in_batch, prefix=f"Backfill batch {batches_processed+1}: ")

                # Update successful descriptions
                if updates:
                    updated = self.scraper.db.update_job_descriptions(updates)
                    total_updated += updated
                    logger.info(f"Updated descriptions for {updated} jobs in this batch")

                # Mark failed attempts to avoid retrying
                if failed_job_ids:
                    marked = self.scraper.db.mark_description_fetch_failed(failed_job_ids)
                    total_failed += marked
                    logger.info(f"Marked {marked} jobs as failed to avoid retrying")

                if not updates and not failed_job_ids:
                    logger.info("No descriptions could be fetched and no failures marked in this batch")

                batches_processed += 1

                # brief pause between batches with feedback
                if batches_processed < max_batches:  # Don't pause after last batch
                    self._log_wait("Pausing between backfill batches")
                    time.sleep(2.0)

            logger.success(f"Backfill complete. Descriptions updated: {total_updated}, Failed/skipped: {total_failed}")
            return True
        except Exception as e:
            logger.error(f"Backfill error: {e}")
            return False