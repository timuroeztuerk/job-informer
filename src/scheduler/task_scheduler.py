"""
Task Scheduler Module
Handles manual execution of job scraping tasks
"""

from typing import List
from datetime import datetime
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
            
            # Scrape jobs from all sources
            jobs_df = self.scraper.scrape_all_sources(keywords, locations)
            
            if not jobs_df.empty:
                # Save jobs to CSV
                csv_filename = self.scraper.save_jobs_to_csv(jobs_df)
                
                # Send email notification
                subject = f"Job Search Report - {len(jobs_df)} opportunities found!"
                self.email_sender.send_job_report(jobs_df, subject)
                
                logger.success(f"Job search completed successfully. Found {len(jobs_df)} jobs.")
                logger.info(f"Results saved to: {csv_filename}")
                return True
            else:
                logger.warning("No jobs found in this search.")
                
                # Send notification about empty results
                subject = "Job Search Report - No new opportunities found"
                self.email_sender.send_job_report(jobs_df, subject)
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
            'max_retries': self.config.max_retries
        }
    
    def generate_full_summary(self) -> bool:
        """Generate and send a summary of all historical job data"""
        logger.info("=== Generating Full Summary Report ===")
        
        try:
            # Load all historical job data
            all_jobs_df = self.scraper.load_all_historical_jobs()
            
            if all_jobs_df.empty:
                logger.warning("No historical job data found")
                subject = "Job Informer Full Summary - No Data Available"
                self.email_sender.send_job_report(all_jobs_df, subject)
                return False
            
            # Remove duplicates across all data
            unique_jobs_df = all_jobs_df.drop_duplicates(subset=['title', 'company'], keep='first')
            
            logger.info(f"Full summary: {len(unique_jobs_df)} unique jobs from {len(all_jobs_df)} total records")
            
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
        """Generate and send a summary of the latest job runs"""
        logger.info(f"=== Generating Latest Summary Report (last {num_runs} runs) ===")
        
        try:
            # Load jobs from the latest runs
            latest_jobs_df = self.scraper.load_latest_historical_jobs(num_runs)
            
            if latest_jobs_df.empty:
                logger.warning("No recent job data found")
                subject = f"Job Informer Latest Summary (last {num_runs} runs) - No Data Available"
                self.email_sender.send_job_report(latest_jobs_df, subject)
                return False
            
            # Remove duplicates across latest data
            unique_jobs_df = latest_jobs_df.drop_duplicates(subset=['title', 'company'], keep='first')
            
            logger.info(f"Latest summary: {len(unique_jobs_df)} unique jobs from {len(latest_jobs_df)} records in last {num_runs} runs")
            
            # Send email with latest summary
            subject = f"Job Informer Latest Summary - {len(unique_jobs_df)} Jobs from Last {num_runs} Runs"
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
        """Apply current filters to historical data and send filtered results"""
        logger.info("=== Filtering Historical Jobs ===")
        
        try:
            # Filter all historical jobs using current filters
            filtered_jobs_df = self.scraper.filter_historical_jobs()
            
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
