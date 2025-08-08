"""
Task Scheduler Module
Handles manual execution of job scraping tasks
"""

from typing import List
from datetime import datetime, timedelta
import time
import zoneinfo
import pandas as pd
from loguru import logger
from ..config.settings import Config
from ..scrapers.job_scraper import JobScraper
from ..email_notifier.email_sender import EmailSender
from ..analyst.market_analyst import MarketAnalyst


class TaskScheduler:
    """Handles manual execution of job scraping and notification tasks"""
    
    def __init__(self, config: Config):
        self.config = config
        self.scraper = JobScraper(config)
        self.email_sender = EmailSender(config)
        self.analyst = MarketAnalyst(config)
        
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
                # Store jobs in SQLite database (fast deduplication)
                new_jobs_count = self.scraper.db.upsert_jobs(jobs_df)
                
                # Export to CSV for backup/email attachment
                csv_filename = self.scraper.save_jobs_to_csv(jobs_df)
                
                # Send email notification (respect dry run)
                subject = f"Job Search Report - {new_jobs_count} new opportunities found!"
                if not self.config.dry_run:
                    # Attach CSV for convenience
                    self.email_sender.send_with_attachment(subject, "See attached CSV for full results.", csv_filename)
                else:
                    logger.info("DRY_RUN is enabled; skipping email send")
                
                logger.success(f"Job search completed successfully. Found {new_jobs_count} new jobs.")
                logger.info(f"Results saved to: {csv_filename}")
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
    
    def test_email_connection(self) -> bool:
        """Test email configuration by sending a test email"""
        logger.info("Testing email connection...")
        
        try:
            success = self.email_sender.send_test_email()
            if success:
                logger.success("Email test successful!")
            else:
                logger.error("Email test failed!")
            return success
        except Exception as e:
            logger.error(f"Email test error: {e}")
            return False
    
    def run_quick_test(self) -> bool:
        """Run a quick test of core functionality"""
        logger.info("Running quick functionality test...")
        
        try:
            # Test configuration
            self.config.validate()
            logger.success("Configuration validation passed")
            
            # Test email connection
            if not self.test_email_connection():
                return False
            
            # Test scraper setup (without actual scraping)
            logger.info("Testing scraper initialization...")
            if self.scraper:
                logger.success("Scraper initialized successfully")
            
            logger.success("All tests passed!")
            return True
            
        except Exception as e:
            logger.error(f"Quick test failed: {e}")
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
            'enable_indeed': self.config.enable_indeed,
            'dry_run': self.config.dry_run
        }
    
    def generate_full_summary(self) -> bool:
        """Generate and send a summary of all historical job data from database"""
        logger.info("=== Generating Full Summary Report ===")
        
        try:
            # Get all jobs from database
            with self.scraper.db._get_connection() as conn:
                all_jobs_df = pd.read_sql_query("SELECT * FROM jobs ORDER BY scraped_at DESC", conn)
            
            if all_jobs_df.empty:
                logger.warning("No historical job data found in database")
                subject = "Job Informer Full Summary - No Data Available"
                self.email_sender.send_job_report(all_jobs_df, subject)
                return False
            
            # Remove duplicates across all data (database should already be deduplicated, but just in case)
            unique_jobs_df = all_jobs_df.drop_duplicates(subset=['job_id'], keep='first')
            
            logger.info(f"Full summary: {len(unique_jobs_df)} unique jobs from database")
            
            # Send email with full summary
            subject = f"Job Informer Full Summary - {len(unique_jobs_df)} Total Unique Jobs"
            success = self.email_sender.send_job_report(unique_jobs_df, subject)
            
            if success:
                logger.success("Full summary report sent successfully")
            else:
                logger.error("Failed to send full summary report")
                
            return success
            
        except Exception as e:
            logger.error(f"Error generating full summary: {e}")
            self.email_sender.send_error_notification(str(e))
            return False
    
    def generate_latest_summary(self, num_runs: int = 5) -> bool:
        """Generate and send a summary of the latest job runs from database"""
        logger.info(f"=== Generating Latest Summary Report (last {num_runs} runs) ===")
        
        try:
            # Get recent jobs from database (last 30 days by default)
            latest_jobs_df = self.scraper.db.get_recent_jobs(days=30)
            
            if latest_jobs_df.empty:
                logger.warning("No recent job data found in database")
                subject = f"Job Informer Latest Summary (last {num_runs} runs) - No Data Available"
                self.email_sender.send_job_report(latest_jobs_df, subject)
                return False
            
            # Remove duplicates (database should already be deduplicated, but just in case)
            unique_jobs_df = latest_jobs_df.drop_duplicates(subset=['job_id'], keep='first')
            
            logger.info(f"Latest summary: {len(unique_jobs_df)} unique jobs from database")
            
            # Send email with latest summary
            subject = f"Job Informer Latest Summary - {len(unique_jobs_df)} Recent Jobs"
            success = self.email_sender.send_job_report(unique_jobs_df, subject)
            
            if success:
                logger.success("Latest summary report sent successfully")
            else:
                logger.error("Failed to send latest summary report")
                
            return success
            
        except Exception as e:
            logger.error(f"Error generating latest summary: {e}")
            self.email_sender.send_error_notification(str(e))
            return False
    
    def filter_and_send_historical_jobs(self) -> bool:
        """Apply current filters to historical data from database and send filtered results"""
        logger.info("=== Filtering Historical Jobs ===")
        
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
            
            if success:
                logger.success("Filtered historical data report sent successfully")
            else:
                logger.error("Failed to send filtered historical data report")
                
            return success
            
        except Exception as e:
            logger.error(f"Error filtering historical jobs: {e}")
            self.email_sender.send_error_notification(str(e))
            return False
    
    def generate_market_report(self) -> bool:
        """Generate and send AI-powered job market analysis report"""
        logger.info("=== Generating AI Market Report ===")
        
        try:
            # Load all historical job data and apply current filters
            filtered_jobs_df = self.scraper.filter_historical_jobs()
            
            if filtered_jobs_df.empty:
                logger.warning("No job data available after filtering for market analysis")
                subject = "Job Market Analysis - No Filtered Data Available"
                self.email_sender.send_error_notification("No job data available after applying current filters for analysis")
                return False
            
            logger.info(f"Analyzing {len(filtered_jobs_df)} filtered historical job records...")
            
            # Generate market report using AI on filtered data
            market_report = self.analyst.generate_market_report(filtered_jobs_df)
            
            if not market_report:
                logger.error("Failed to generate market analysis report")
                subject = "Job Market Analysis - Generation Failed"
                self.email_sender.send_error_notification("Failed to generate AI market analysis report")
                return False
            
            # Send market report via email
            subject = f"AI Job Market Analysis Report - {len(filtered_jobs_df)} Filtered Jobs Analyzed"
            success = self.email_sender.send_market_report(market_report, subject, len(filtered_jobs_df))
            
            if success:
                logger.success("AI market analysis report sent successfully")
            else:
                logger.error("Failed to send market analysis report")
                
            return success
            
        except Exception as e:
            logger.error(f"Error generating market report: {e}")
            self.email_sender.send_error_notification(str(e))
            return False
    
    def test_ai_connection(self) -> bool:
        """Test AI (Gemini) API connection"""
        logger.info("Testing AI (Gemini) API connection...")
        
        try:
            success = self.analyst.test_api_connection()
            
            if success:
                logger.success("AI API connection test passed")
            else:
                logger.error("AI API connection test failed")
                
            return success
            
        except Exception as e:
            logger.error(f"Error testing AI connection: {e}")
            return False
            return False

    def _seconds_until_next_time(self, hhmm: str, tz: str) -> int:
        tzinfo = zoneinfo.ZoneInfo(tz)
        now = datetime.now(tzinfo)
        hour, minute = map(int, hhmm.split(':'))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        return max(1, int((target - now).total_seconds()))

    def start_daily_schedule(self) -> bool:
        """Run job search daily at configured time and timezone. Blocks current thread."""
        try:
            logger.info(f"Schedule configured at {self.config.schedule_time} ({self.config.timezone})")
            while True:
                seconds = self._seconds_until_next_time(self.config.schedule_time, self.config.timezone)
                logger.info(f"Sleeping for {seconds//3600}h{(seconds%3600)//60}m until next run...")
                time.sleep(seconds)
                run_id = datetime.now().strftime('%Y%m%d-%H%M%S')
                logger.info(f"[run_id={run_id}] Executing scheduled job search")
                try:
                    self.execute_job_search()
                except Exception as e:
                    self.email_sender.send_error_notification(str(e))
        except KeyboardInterrupt:
            logger.info("Schedule interrupted by user")
            return False
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            self.email_sender.send_error_notification(str(e))
            return False
