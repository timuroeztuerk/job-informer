#!/usr/bin/env python3
"""
Job Informer - Main Application Entry Point
Automated job posting monitoring and notification system
"""

import argparse
import sys
import os
from pathlib import Path
import pandas as pd

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.config.settings import Config
from src.utils.logging_utils import setup_logging
from src.scrapers.job_scraper import JobScraper
from src.scheduler.task_scheduler import TaskScheduler
from src.email_notifier.email_sender import EmailSender
from loguru import logger


def create_data_directory():
    """Create data directory if it doesn't exist"""
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    return data_dir


def main():
    """Main application entry point"""
    parser = argparse.ArgumentParser(description="Job Informer - Automated job monitoring system")
    
    # Add command line arguments
    parser.add_argument(
        '--config',
        type=str,
        help='Path to .env configuration file (default: .env)'
    )
    
    parser.add_argument(
        '--mode',
        choices=['run-once', 'test', 'summary', 'market-report', 'schedule', 'db-summary', 'migrate-csv', 'purge', 'backfill-descriptions'],
        default='run-once',
        help='Execution mode (default: run-once)'
    )
    
    parser.add_argument(
        '--keywords',
        type=str,
        help='Job search keywords (comma-separated)'
    )
    
    parser.add_argument(
        '--locations',
        type=str,
        help='Search locations (comma-separated)'
    )

    parser.add_argument(
        '--web',
        type=str,
        help='Sources to scrape (comma-separated): linkedin,porsche. If omitted, uses .env toggles'
    )
    
    parser.add_argument(
        '--schedule-time',
        type=str,
        help='Daily schedule time (HH:MM format)'
    )
    
    args = parser.parse_args()
    
    try:
        # Load configuration
        config = Config.from_env(args.config)
        
        # Override config with command line arguments if provided
        if args.keywords:
            config.search_keywords = args.keywords
        if args.locations:
            config.search_locations = args.locations
        # Sources selection: only override when --web is provided; otherwise respect .env
        if args.web:
            selected_sources = [s.strip().lower() for s in args.web.split(',') if s.strip()]
            config.enable_linkedin = ('linkedin' in selected_sources)
            try:
                config.enable_porsche = ('porsche' in selected_sources)
            except Exception:
                pass
        if args.schedule_time:
            config.schedule_time = args.schedule_time
        
        # Validate configuration for the selected mode
        config.validate_for_mode(args.mode)
        
        # Setup logging
        setup_logging(config.log_level, config.log_file)
        
        logger.info("Starting Job Informer application")
        logger.info("Providing periodic feedback during waits and backoffs")
        logger.info(f"Mode: {args.mode}")
        
        # Create data directory
        create_data_directory()
        
        # Execute based on mode
        scheduler = TaskScheduler(config)

        # Optional automatic CSV migration when enabled via env flag
        try:
            if getattr(config, 'auto_migrate_csv', False):
                logger.info("AUTO_MIGRATE_CSV is enabled — migrating CSV files to database before running mode")
                migrate_csv_files(scheduler)
        except Exception as e:
            logger.warning(f"Automatic CSV migration skipped due to error: {e}")
        
        if args.mode == 'run-once':
            run_job_search_once(scheduler)
        elif args.mode == 'test':
            run_all_tests(scheduler)
            # If DRY_RUN is enabled, chain a fast dry-run to preview scraping without side effects
            try:
                if scheduler.config.dry_run:
                    from loguru import logger as _logger
                    _logger.info("DRY_RUN=true detected — executing fast dry run after tests")
                    scheduler.run_dry_run()
            except Exception as _e:
                logger.warning(f"Fast dry run skipped due to error: {_e}")
        elif args.mode == 'summary':
            run_latest_summary(scheduler)
        elif args.mode == 'market-report':
            generate_market_report(scheduler)
        elif args.mode == 'schedule':
            start_schedule(scheduler)
        elif args.mode == 'db-summary':
            show_database_summary(scheduler)
        elif args.mode == 'migrate-csv':
            migrate_csv_files(scheduler)
        elif args.mode == 'purge':
            purge_unwanted_jobs(scheduler)
        elif args.mode == 'backfill-descriptions':
            backfill_descriptions(scheduler)
        else:
            logger.error(f"Unsupported mode: {args.mode}")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)


def test_email_configuration(scheduler: TaskScheduler):
    """Test email configuration"""
    logger.info("=== Testing Email Configuration ===")
    
    success = scheduler.test_email_connection()
    
    if success:
        logger.success("Email test successful! Check your inbox.")
    else:
        logger.error("Email test failed. Please check your configuration.")
        sys.exit(1)


def run_job_search_once(scheduler: TaskScheduler):
    """Run job search once and send results"""
    logger.info("=== Starting Job Search ===")
    
    # Show search configuration
    summary = scheduler.get_search_summary()
    logger.info(f"Keywords: {', '.join(summary['keywords'])}")
    logger.info(f"Locations: {', '.join(summary['locations'])}")
    logger.info(f"Email configured: {summary['email_configured']}")
    
    # Execute the search
    success = scheduler.execute_job_search()
    
    if success:
        logger.success("=== Job Search Completed Successfully ===")
    else:
        logger.warning("=== Job Search Completed (No results found) ===")


def run_quick_test(scheduler: TaskScheduler):
    """Run quick functionality test"""
    logger.info("=== Running Quick Test ===")
    
    success = scheduler.run_quick_test()
    
    if success:
        logger.success("=== All Tests Passed ===")
    else:
        logger.error("=== Some Tests Failed ===")
        sys.exit(1)


def run_all_tests(scheduler: TaskScheduler):
    """Run combined tests: config/email test, scraper init, and AI connection"""
    logger.info("=== Running Combined Tests ===")
    overall_success = True
    
    # Quick test includes config validation, email test, and scraper init
    if not scheduler.run_quick_test():
        overall_success = False
    
    # AI connection test
    if not scheduler.test_ai_connection():
        overall_success = False
    
    if overall_success:
        logger.success("=== All Tests Passed ===")
    else:
        logger.error("=== Tests Failed ===")
        sys.exit(1)


def run_full_summary(scheduler: TaskScheduler):
    """Generate and send full summary report"""
    logger.info("=== Generating Full Summary ===")
    
    success = scheduler.generate_full_summary()
    
    if success:
        logger.success("=== Full Summary Sent Successfully ===")
    else:
        logger.error("=== Full Summary Failed ===")
        sys.exit(1)


def run_latest_summary(scheduler: TaskScheduler):
    """Generate and send latest summary report"""
    logger.info("=== Generating Latest Summary ===")
    
    success = scheduler.generate_latest_summary()
    
    if success:
        logger.success("=== Latest Summary Sent Successfully ===")
    else:
        logger.error("=== Latest Summary Failed ===")
        sys.exit(1)




def generate_market_report(scheduler: TaskScheduler):
    """Generate and send AI-powered market analysis report"""
    logger.info("=== Generating AI Market Report ===")
    
    success = scheduler.generate_market_report()
    
    if success:
        logger.success("=== AI Market Report Sent Successfully ===")
    else:
        logger.error("=== AI Market Report Generation Failed ===")
        sys.exit(1)


def test_ai_connection(scheduler: TaskScheduler):
    """Test AI (Gemini) API connection"""
    logger.info("=== Testing AI Connection ===")
    
    success = scheduler.test_ai_connection()
    
    if success:
        logger.success("=== AI Connection Test Passed ===")
    else:
        logger.error("=== AI Connection Test Failed ===")
        sys.exit(1)


def start_schedule(scheduler: TaskScheduler):
    """Start daily schedule loop"""
    logger.info("=== Starting Schedule Mode ===")
    success = scheduler.start_daily_schedule()
    if not success:
        logger.error("=== Schedule loop terminated with errors ===")
        sys.exit(1)


def show_database_summary(scheduler: TaskScheduler):
    """Show database summary statistics"""
    logger.info("=== Database Summary ===")
    summary = scheduler.scraper.db.get_job_summary()
    
    print(f"\n📊 Job Database Summary:")
    print(f"Total jobs: {summary['total_jobs']:,}")
    print(f"Recent jobs (7 days): {summary['recent_jobs_7_days']:,}")
    print(f"Date range: {summary['date_range']['earliest']} to {summary['date_range']['latest']}")
    
    print(f"\n📈 Jobs by source:")
    for source, count in summary['jobs_by_source'].items():
        print(f"  {source}: {count:,}")
    
    print(f"\n🏢 Top companies:")
    for company, count in list(summary['top_companies'].items())[:5]:
        print(f"  {company}: {count:,}")
    
    # City/description coverage breakdown (full DB)
    try:
        with scheduler.scraper.db._get_connection() as conn:
            all_jobs_df = pd.read_sql_query("SELECT location, description FROM jobs", conn)
        scheduler._print_city_and_description_summary(all_jobs_df, title="DB City & Description Coverage")
    except Exception as e:
        logger.warning(f"Could not compute DB city/description summary: {e}")

    logger.success("Database summary displayed")


def migrate_csv_files(scheduler: TaskScheduler):
    """Migrate existing CSV files to SQLite database"""
    logger.info("=== Migrating CSV Files to Database ===")
    
    data_dir = Path("data")
    csv_files = list(data_dir.glob("jobs_*.csv"))
    
    if not csv_files:
        logger.warning("No CSV files found to migrate")
        return
    
    total_migrated = 0
    for csv_file in csv_files:
        logger.info(f"Migrating {csv_file.name}...")
        migrated = scheduler.scraper.db.migrate_csv_to_sqlite(str(csv_file))
        total_migrated += migrated
    
    logger.success(f"Migration complete: {total_migrated} total jobs migrated")


def purge_unwanted_jobs(scheduler: TaskScheduler):
    """Purge unwanted jobs from the database based on current filters"""
    logger.info("=== Purging Unwanted Jobs from Database ===")
    success = scheduler.purge_unwanted_jobs()
    
    if success:
        logger.success("=== Database Purge Completed Successfully ===")
    else:
        logger.error("=== Database Purge Failed ===")
        sys.exit(1)


def backfill_descriptions(scheduler: TaskScheduler):
    """Backfill missing job descriptions in the database"""
    logger.info("=== Backfilling Missing Job Descriptions ===")
    success = scheduler.backfill_missing_descriptions()
    if success:
        logger.success("=== Backfill Completed Successfully ===")
    else:
        logger.error("=== Backfill Failed ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
