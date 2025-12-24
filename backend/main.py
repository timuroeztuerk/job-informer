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
from loguru import logger


# Ensure paths resolve relative to backend directory
ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

# Add src directory to Python path
sys.path.insert(0, str(ROOT / "src"))

from src.config.settings import Config
from src.agents.job_scraper import JobScraper
from src.agents.parser import DescriptionTools
from src.agents.email_sender import EmailSender
from src.agents.ai_purger import AIPurger
from src.utils.logging_utils import setup_logging
from src.utils.database import JobDatabase
from src.utils.db_summary import build_db_summary

def create_data_directory():
    """Create data directory if it doesn't exist"""
    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    return data_dir


def main():
    """Main application entry point"""
    parser = argparse.ArgumentParser(description="Job Informer - Automated job monitoring system")
    
    parser.add_argument(
        '--config',
        type=str,
        help='Path to .env configuration file (default: .env)'
    )
    # Execution modes: choose one flag; defaults to run-once if none provided
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--run-once', dest='run_once', action='store_true', help='Run job search once')
    group.add_argument('--test', action='store_true', help='Run combined tests')
    group.add_argument('--db-summary', dest='db_summary', action='store_true', help='Show database summary')
    group.add_argument('--purge', action='store_true', help='Purge unwanted jobs (rules + AI)')
    group.add_argument(
        '--ai-purge',
        dest='ai_purge',
        action='store_true',
        help='[Deprecated] Alias for --purge (runs rules + AI purge)'
    )
    group.add_argument(
        '--parse-descriptions',
        dest='parse_descriptions',
        action='store_true',
        help='Fetch missing job descriptions and parse them with AI'
    )
    group.add_argument(
        '--get-descriptions',
        dest='get_descriptions',
        action='store_true',
        help='[Deprecated] Alias for --parse-descriptions (fetch + parse descriptions)'
    )
    group.add_argument('--reset-ai-purge', dest='reset_ai_purge', action='store_true', help='Reset AI purge analysis flags')
    group.add_argument(
        '--refetch-titles',
        dest='refetch_titles',
        action='store_true',
        help='Refetch job titles/companies from stored URLs when they look masked'
    )
    group.add_argument(
        '--inform',
        dest='inform',
        nargs='?',
        const='last',
        choices=['last', 'random'],
        help='Email a digest of jobs (default: last 25)'
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
        '--time',
        dest='time_range',
        choices=['day', 'week', 'month'],
        help='Filter jobs posted within the specified time range'
    )

    args = parser.parse_args()

    used_deprecated_ai_flag = getattr(args, 'ai_purge', False)
    used_deprecated_flag = getattr(args, 'get_descriptions', False)
    # Unify deprecated flag with the new combined workflow
    if used_deprecated_ai_flag:
        args.purge = True
    if used_deprecated_flag:
        args.parse_descriptions = True

    # Start with default logging so early errors are visible
    setup_logging()
    if used_deprecated_ai_flag:
        logger.warning("--ai-purge is deprecated; use --purge for the combined purge pipeline")
    if used_deprecated_flag:
        logger.warning("--get-descriptions is deprecated; use --parse-descriptions for the combined workflow")
    # Default to run-once if no mode flag is set
    if not any([args.run_once, args.test, args.db_summary, args.purge, args.ai_purge, args.parse_descriptions, args.reset_ai_purge, args.inform, args.refetch_titles]):
        args.run_once = True
    # Determine selected mode string for config validation and logging
    if args.run_once:
        mode_str = 'run-once'
    elif args.test:
        mode_str = 'test'
    elif args.db_summary:
        mode_str = 'db-summary'
    elif args.purge:
        mode_str = 'purge'
    elif args.ai_purge:
        mode_str = 'purge'
    elif args.inform:
        mode_str = 'inform'
    elif args.refetch_titles:
        mode_str = 'refetch-titles'
    elif args.parse_descriptions:
        mode_str = 'parse-descriptions'
    elif args.reset_ai_purge:
        mode_str = 'reset-ai-purge'
    else:
        mode_str = 'run-once'
    
    try:
        # Load configuration
        config = Config.from_env(args.config)

        # Reconfigure logging based on settings
        setup_logging(config.log_level, config.log_file)
        # Override config with command line arguments if provided
        if args.keywords:
            config.search_keywords = args.keywords
        if args.locations:
            config.search_locations = args.locations
        if args.time_range:
            config.search_time_range = args.time_range
        # Validate configuration for the selected mode
        config.validate_for_mode(mode_str)
        create_data_directory()
        
        if args.run_once:
            run_job_search_once(config)  
        elif args.test:
            run_all_tests(config)
        elif args.db_summary:
            show_database_summary(config)
        elif args.purge:
            run_purge_pipeline(config)
        elif args.ai_purge:
            run_purge_pipeline(config)
        elif args.inform:
            run_inform_mode(config, args.inform)
        elif args.refetch_titles:
            run_title_backfill(config)
        elif args.parse_descriptions:
            run_description_pipeline(config)
        elif args.reset_ai_purge:
            reset_ai_purge_flags(config)
        else:
            logger.error(f"Unsupported mode: {mode_str}")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)

def run_job_search_once(config: Config):
    """Run the Job Scraper"""
    scraper = JobScraper(config)
    summary = scraper.get_search_summary()
    keywords_str = ', '.join(summary['keywords'])
    locations_str = ', '.join(summary['locations'])
    logger.info(
        f"Starting, Keywords: {keywords_str}, Locations: {locations_str}, Time range: {summary['time_range']}"
    )
    
    success = scraper.execute_job_search()
    if success:
        logger.success("=== Job Search Done ===")
    elif getattr(scraper, 'last_run_threshold_hit', False):
        logger.warning("=== Job Search skipped: below MIN_NEW_JOBS_TO_CONTINUE threshold ===")
    else:
        logger.warning("=== No Jobs Found ===")

def run_all_tests(config: Config):
    """Run combined tests: config/email test, scraper init, and AI connection"""
    overall_success = True
    
    try:
        config.validate()
        try:
            email_sender = EmailSender(config)
            success = email_sender.send_test_email()
            if success:
                pass
            else:
                logger.error("Email test failed!")
                overall_success = False
        except Exception as e:
            logger.error(f"Email test error: {e}")
            overall_success = False
            
        # Test scraper initialization
        try:
            scraper = JobScraper(config)
            if scraper:
                pass
            else:
                logger.error("Scraper initialization failed")
                overall_success = False
        except Exception as e:
            logger.error(f"Scraper initialization error: {e}")
            overall_success = False
        
        # Test LLM connection (if API key is configured)
        try:
            if getattr(config, 'openai_api_key', ''):
                # Initialize AI Purger with test mode enabled
                ai_purger = AIPurger(config, test_mode=True)
                # Test LLM connection by checking if API key is available
                if getattr(ai_purger.llm, 'api_key', ''):
                    logger.info("AI LLM connection test passed (API key configured)")
                else:
                    logger.error("AI LLM connection failed (no API key)")
                    overall_success = False
            else:
                logger.info("AI LLM test skipped (no API key configured)")
        except Exception as e:
            logger.error(f"AI LLM test error: {e}")
            logger.warning("AI LLM test failed, but continuing with other tests")
        
        # Final result
        if overall_success:
            logger.success("All tests passed!")
        else:
            logger.error("Some tests failed")
            
    except Exception as e:
        logger.error(f"Test failed: {e}")
        overall_success = False

def show_database_summary(config: Config):
    """Show database summary statistics"""
    scraper = JobScraper(config)
    summary = build_db_summary(scraper.db)

    totals = summary.get("totals", {})
    print(f"\n📊 Job Database Summary:")
    print(f"Total jobs: {totals.get('total_jobs', 0):,}")
    print(f"Recent jobs (7 days): {totals.get('recent_jobs_7_days', 0):,}")
    date_range = totals.get("date_range") or {}
    print(f"Date range: {date_range.get('earliest')} to {date_range.get('latest')}")

    parsed = summary.get("parsed_insights") or {}
    parsed_count = parsed.get("total_records", 0) or 0
    if parsed_count:
        _print_top_list("💻 Top Programming Languages:", parsed.get("programming_languages") or [], limit=5)
        _print_top_list("🎯 Top Skills:", parsed.get("skills") or [], limit=5)
        _print_top_list("🔧 Top Tools & Technologies:", parsed.get("tools") or [], limit=10)
        _print_top_list("📊 Seniority Levels:", parsed.get("seniority_levels") or [], limit=3)
        _print_top_list("💼 Employment Types:", parsed.get("employment_types") or [], limit=3)
        _print_top_list("🗣️ Top Job Languages:", parsed.get("languages") or [], limit=3)
        _print_top_list("📜 Degree Fields:", parsed.get("degree_fields") or [], limit=5)

        exp = parsed.get("experience_years") or {}
        if exp.get("count"):
            print(f"\n📈 Experience Requirements:")
            print(f"  Jobs with experience data: {exp['count']:,}")
            print(f"  Average experience: {exp.get('average', 0) or 0:.1f} years")
            print(f"  Range: {exp.get('min', 0)}-{exp.get('max', 0)} years")

        salary = parsed.get("salary_eur") or {}
        if salary.get("count"):
            print(f"\n💰 Salary Information:")
            print(f"  Jobs with salary data: {salary['count']:,}")
            avg_salary = salary.get("average") or 0
            min_salary = salary.get("min") or 0
            max_salary = salary.get("max") or 0
            print(f"  Average salary: €{avg_salary:,.0f}")
            print(f"  Range: €{min_salary:,.0f} - €{max_salary:,.0f}")
    else:
        print(f"\n🧠 No parsed descriptions found - run 'python main.py --parse-descriptions' first")

    city_summary = summary.get("city_summary") or {}
    top_cities = city_summary.get("top_cities") or []
    if top_cities:
        print(f"\n🏙️  Top 5 cities:")
        for i, city in enumerate(top_cities, 1):
            percentage = city.get("percentage", 0.0)
            print(f"  {i}. {city.get('name')}: {city.get('count', 0):,} jobs ({percentage:.1f}%)")
    else:
        print(f"\n==== Top 5 Cities ====\nNo jobs to summarize.\n")


def _print_top_list(title: str, entries: list[dict], *, limit: int | None = None):
    if not entries:
        return
    print(f"\n{title}")
    for entry in entries[:limit]:
        percentage = entry.get("percentage")
        pct_str = f" ({percentage:.1f}%)" if percentage is not None else ""
        print(f"  {entry.get('name')}: {entry.get('count', 0):,}{pct_str}")

def purge_unwanted_jobs(config: Config):
    """Purge unwanted jobs from database"""
    scraper = JobScraper(config)
    try:
        logger.info("Starting database purge...")
        success = scraper.purge_unwanted_jobs()
        
        if success:
            logger.success("=== Database Purge Completed Successfully ===")
        else:
            logger.error("=== Database Purge Failed ===")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Database purge error: {e}")
        sys.exit(1)

def run_purge_pipeline(config: Config):
    """Run rule-based purge, then AI purge."""
    logger.info("=== Running purge pipeline (rules + AI) ===")
    purge_unwanted_jobs(config)
    run_ai_purge_mode(config)

def run_ai_purge_mode(config: Config):
    """Run AI-powered job purging using LLM analysis"""
    logger.info("=== Starting AI-Powered Job Purging ===")
    
    try:
        # Initialize AI Purger
        ai_purger = AIPurger(config)
        
        # Test LLM connection first
        if hasattr(ai_purger.llm, 'api_key') and ai_purger.llm.api_key:
            logger.info("AI LLM API key is configured - proceeding with purge")
        else:
            logger.error("Failed to connect to LLM - no API key configured")
            sys.exit(1)
        
        # Run the AI purge process
        summary = ai_purger.run_purge_mode()
        
        # Log summary
        logger.info(f"AI Purge Summary: Analyzed {summary['jobs_analyzed']:,} jobs in {summary.get('batches_processed', 0)} batches")
        logger.info(f"Jobs marked for purging: {summary['jobs_to_purge']:,}, Jobs actually purged: {summary['jobs_purged']:,}")
        
        if summary['success']:
            logger.success("=== Done ===")
        else:
            error_msg = summary.get('error', 'Unknown error')
            logger.error(f"=== AI-Powered Purge Failed: {error_msg} ===")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"AI purge mode failed: {e}")
        sys.exit(1)

def run_inform_mode(config: Config, selection: str):
    """Send an email digest of jobs from the database"""
    logger.info("=== Preparing job digest email ===")
    db = JobDatabase()
    email_sender = EmailSender(config)

    try:
        with db._get_connection() as conn:
            if selection == 'random':
                query = """
                    SELECT job_id, title, company, location, source, url, salary, scraped_at
                    FROM jobs
                    ORDER BY RANDOM()
                    LIMIT 25
                """
            else:
                query = """
                    SELECT job_id, title, company, location, source, url, salary, scraped_at
                    FROM jobs
                    ORDER BY COALESCE(created_at, scraped_at) DESC
                    LIMIT 25
                """
            jobs_df = pd.read_sql_query(query, conn)

        if jobs_df.empty:
            logger.warning("No jobs available in the database to include in the digest")
            return

        jobs_df = jobs_df.fillna({
            'title': '',
            'company': '',
            'location': '',
            'source': '',
            'salary': 'Not specified',
            'url': ''
        })

        subject_descriptor = "Random 25 jobs" if selection == 'random' else "Latest 25 jobs"
        subject = f"Job Informer Digest - {subject_descriptor}"

        if email_sender.send_job_report(jobs_df, subject):
            logger.success("=== Job digest email sent ===")
        else:
            logger.error("Failed to send job digest email")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Inform mode failed: {e}")
        try:
            email_sender.send_error_notification(str(e))
        except Exception as notify_error:
            logger.error(f"Failed to send inform error notification: {notify_error}")
        sys.exit(1)

def reset_ai_purge_flags(config: Config, *, reset_all: bool = True):
    """Reset AI purge analyzed flags so jobs can be reconsidered"""
    logger.info("=== Resetting AI purge analysis flags ===")
    
    try:
        ai_purger = AIPurger(config)
        reset_count = ai_purger.reset_analyzed_flags(all_jobs=reset_all)
        logger.info(f"Reset analyzed flag for {reset_count} job records")
        logger.success("=== Done ===")
    except Exception as e:
        logger.error(f"Failed to reset AI purge flags: {e}")
        sys.exit(1)

def run_description_pipeline(config: Config):
    """Fetch missing descriptions, then parse them with AI."""
    desc = DescriptionTools(config)
    scraper = JobScraper(config)

    logger.info("=== Fetching missing job descriptions ===")
    backfill_success = desc.backfill_missing_descriptions(scraper)
    if not backfill_success:
        logger.error("=== Description backfill failed ===")
        sys.exit(1)

    logger.info("=== Parsing job descriptions with AI ===")
    parse_success = desc.run_description_parser()
    if parse_success:
        logger.success("=== Done ===")
    else:
        logger.error("=== Description Parsing Failed ===")
        sys.exit(1)

def run_title_backfill(config: Config, limit: int = 200):
    """Refetch masked job titles/companies from stored URLs."""
    scraper = JobScraper(config)
    result = scraper.backfill_masked_titles(limit=limit)
    updated = result.get("updated", 0)
    failed = result.get("failed", 0)
    logger.info("Titles refetched: updated=%d, failed=%d", updated, failed)
    if updated:
        logger.success("=== Title backfill completed ===")
    else:
        logger.warning("=== No titles updated ===")

if __name__ == "__main__":
    main()
