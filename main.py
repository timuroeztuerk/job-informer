#!/usr/bin/env python3
"""
Job Informer - Main Application Entry Point
Automated job posting monitoring and notification system
"""

import argparse
import sys
import os
import json
from pathlib import Path
import pandas as pd

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.config.settings import Config
from src.utils.logging_utils import setup_logging
from src.scrapers.job_scraper import JobScraper
from src.scheduler.task_scheduler import TaskScheduler
from src.email_notifier.email_sender import EmailSender
from src.ai_job_purger.ai_purger import AIPurger
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
        choices=['run-once', 'test', 'summary', 'market-report', 'schedule', 'db-summary', 'migrate-csv', 'purge', 'ai-purge', 'ai-purge-test', 'backfill-descriptions', 'parse-descriptions', 'reset-failed-descriptions', 'cleanup-orphaned-descriptions'],
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
        elif args.mode == 'db-summary':
            show_database_summary(scheduler)
        elif args.mode == 'migrate-csv':
            migrate_csv_files(scheduler)
        elif args.mode == 'purge':
            purge_unwanted_jobs(scheduler)
        elif args.mode == 'ai-purge':
            run_ai_purge_mode(scheduler)
        elif args.mode == 'ai-purge-test':
            run_ai_purge_test_mode(scheduler)
        elif args.mode == 'backfill-descriptions':
            backfill_descriptions(scheduler)
        elif args.mode == 'parse-descriptions':
            run_description_parser(scheduler)
        elif args.mode == 'reset-failed-descriptions':
            reset_failed_descriptions(scheduler)
        elif args.mode == 'cleanup-orphaned-descriptions':
            cleanup_orphaned_descriptions(scheduler)
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

def show_database_summary(scheduler: TaskScheduler):
    """Show database summary statistics"""
    logger.info("=== Database Summary ===")
    summary = scheduler.scraper.db.get_job_summary()
    print(f"\n📊 Job Database Summary:")
    print(f"Total jobs: {summary['total_jobs']:,}")
    print(f"Recent jobs (7 days): {summary['recent_jobs_7_days']:,}")
    print(f"Date range: {summary['date_range']['earliest']} to {summary['date_range']['latest']}")
    # Add parsed description analytics
    try:
        with scheduler.scraper.db._get_connection() as conn:
            # Get parsed descriptions with the latest version for each job
            parsed_df = pd.read_sql_query("""
                SELECT p.job_id, p.payload_json, p.version
                FROM parsed_descriptions p
                INNER JOIN (
                    SELECT job_id, MAX(version) as max_version
                    FROM parsed_descriptions
                    GROUP BY job_id
                ) latest ON p.job_id = latest.job_id AND p.version = latest.max_version
            """, conn)
            
            if not parsed_df.empty:
                programming_languages = {}
                skills = {}
                tools = {}
                seniority_levels = {}
                employment_types = {}
                remote_options = {}
                languages = {}
                degree_fields = {}
                degree_types = {}
                experience_years = []
                salary_ranges = []
                
                for _, row in parsed_df.iterrows():
                    try:
                        data = json.loads(row['payload_json'])
                        
                        # Count programming languages
                        if 'programming_languages' in data and isinstance(data['programming_languages'], list):
                            for lang in data['programming_languages']:
                                if lang and lang.strip():
                                    programming_languages[lang] = programming_languages.get(lang, 0) + 1
                        
                        # Count skills
                        if 'skills' in data and isinstance(data['skills'], list):
                            for skill in data['skills']:
                                if skill and skill.strip():
                                    skills[skill] = skills.get(skill, 0) + 1
                        
                        # Count tools
                        if 'tools' in data and isinstance(data['tools'], list):
                            for tool in data['tools']:
                                if tool and tool.strip():
                                    tools[tool] = tools.get(tool, 0) + 1
                        
                        # Count seniority levels
                        if 'seniority' in data and data['seniority'] and data['seniority'] != 'unspecified':
                            seniority_levels[data['seniority']] = seniority_levels.get(data['seniority'], 0) + 1
                        
                        # Count employment types
                        if 'employment_type' in data and data['employment_type'] and data['employment_type'] != 'unspecified':
                            employment_types[data['employment_type']] = employment_types.get(data['employment_type'], 0) + 1
                        
                        # Count remote options
                        if 'remote' in data and data['remote'] and data['remote'] != 'unspecified':
                            remote_options[data['remote']] = remote_options.get(data['remote'], 0) + 1
                        
                        # Count languages
                        if 'languages' in data and isinstance(data['languages'], list):
                            for lang in data['languages']:
                                if lang and lang.strip():
                                    languages[lang] = languages.get(lang, 0) + 1
                        
                        # Count degree fields
                        if 'degree_field' in data and data['degree_field'] and data['degree_field'] != 'unspecified':
                            degree_fields[data['degree_field']] = degree_fields.get(data['degree_field'], 0) + 1
                        
                        # Count degree types
                        if 'degree_type' in data and data['degree_type'] and data['degree_type'] != 'unspecified':
                            degree_types[data['degree_type']] = degree_types.get(data['degree_type'], 0) + 1
                        
                        # Collect experience years
                        if 'years_experience_min' in data and data['years_experience_min'] is not None:
                            try:
                                years = int(data['years_experience_min'])
                                if 0 <= years <= 20:  # Filter reasonable values
                                    experience_years.append(years)
                            except (ValueError, TypeError):
                                pass
                        
                        # Collect salary ranges
                        if 'salary_eur_range' in data and isinstance(data['salary_eur_range'], dict):
                            salary_min = data['salary_eur_range'].get('min')
                            salary_max = data['salary_eur_range'].get('max')
                            if salary_min is not None or salary_max is not None:
                                salary_ranges.append({'min': salary_min, 'max': salary_max})
                                    
                    except json.JSONDecodeError:
                        continue  # Skip malformed JSON
                
                # Show top programming languages
                if programming_languages:
                    sorted_langs = sorted(programming_languages.items(), key=lambda x: x[1], reverse=True)
                    print(f"\n💻 Top Programming Languages:")
                    for lang, count in sorted_langs[:5]:
                        percentage = (count / len(parsed_df)) * 100
                        print(f"  {lang}: {count:,} ({percentage:.1f}%)")
                
                # Show top skills
                if skills:
                    sorted_skills = sorted(skills.items(), key=lambda x: x[1], reverse=True)
                    print(f"\n🎯 Top Skills:")
                    for skill, count in sorted_skills[:5]:
                        percentage = (count / len(parsed_df)) * 100
                        print(f"  {skill}: {count:,} ({percentage:.1f}%)")
                
                # Show top tools
                if tools:
                    sorted_tools = sorted(tools.items(), key=lambda x: x[1], reverse=True)
                    print(f"\n🔧 Top Tools & Technologies:")
                    for tool, count in sorted_tools[:10]:
                        percentage = (count / len(parsed_df)) * 100
                        print(f"  {tool}: {count:,} ({percentage:.1f}%)")
                
                # Show seniority levels
                if seniority_levels:
                    sorted_seniority = sorted(seniority_levels.items(), key=lambda x: x[1], reverse=True)
                    print(f"\n📊 Seniority Levels:")
                    for level, count in sorted_seniority[:3]:
                        percentage = (count / len(parsed_df)) * 100
                        print(f"  {level}: {count:,} ({percentage:.1f}%)")
                
                # Show employment types
                if employment_types:
                    sorted_employment = sorted(employment_types.items(), key=lambda x: x[1], reverse=True)
                    print(f"\n💼 Employment Types:")
                    for emp_type, count in sorted_employment[:3]:
                        percentage = (count / len(parsed_df)) * 100
                        print(f"  {emp_type}: {count:,} ({percentage:.1f}%)")
                
                # Show top 3 job languages
                if languages:
                    sorted_languages = sorted(languages.items(), key=lambda x: x[1], reverse=True)
                    print(f"\n🗣️ Top 3 Job Languages:")
                    for lang, count in sorted_languages[:3]:
                        percentage = (count / len(parsed_df)) * 100
                        print(f"  {lang}: {count:,} ({percentage:.1f}%)")
                
                # Show degree fields
                if degree_fields:
                    sorted_degree_fields = sorted(degree_fields.items(), key=lambda x: x[1], reverse=True)
                    print(f"\n📜 Degree Fields:")
                    for field, count in sorted_degree_fields[:5]:
                        percentage = (count / len(parsed_df)) * 100
                        print(f"  {field}: {count:,} ({percentage:.1f}%)")
                
                # Show experience statistics
                if experience_years:
                    avg_exp = sum(experience_years) / len(experience_years)
                    min_exp = min(experience_years)
                    max_exp = max(experience_years)
                    print(f"\n📈 Experience Requirements:")
                    print(f"  Jobs with experience data: {len(experience_years):,}")
                    print(f"  Average experience: {avg_exp:.1f} years")
                    print(f"  Range: {min_exp}-{max_exp} years")
                
                # Show salary statistics
                if salary_ranges:
                    valid_salaries = []
                    for salary in salary_ranges:
                        if salary['min'] is not None and salary['max'] is not None:
                            # Use average of min and max for overall stats
                            avg_salary = (salary['min'] + salary['max']) / 2
                            if 20000 <= avg_salary <= 200000:  # Filter reasonable values
                                valid_salaries.append(avg_salary)
                    
                    if valid_salaries:
                        avg_salary = sum(valid_salaries) / len(valid_salaries)
                        min_salary = min(valid_salaries)
                        max_salary = max(valid_salaries)
                        print(f"\n💰 Salary Information:")
                        print(f"  Jobs with salary data: {len(valid_salaries):,}")
                        print(f"  Average salary: €{avg_salary:,.0f}")
                        print(f"  Range: €{min_salary:,.0f} - €{max_salary:,.0f}")
            else:
                print(f"\n🧠 No parsed descriptions found - run 'python main.py --mode parse-descriptions' first")
                
    except Exception as e:
        logger.warning(f"Could not compute parsed descriptions analytics: {e}")
    
    # City/description coverage breakdown
    try:
        with scheduler.scraper.db._get_connection() as conn:
            all_jobs_df = pd.read_sql_query("SELECT location, description FROM jobs", conn)
        
        if all_jobs_df is None or all_jobs_df.empty:
            print(f"\n==== Top 5 Cities ====\nNo jobs to summarize.\n")
        else:
            cities = all_jobs_df['location'].astype(str).str.split(',').str[0].str.strip().replace({'': 'Unknown'})
            city_counts = cities.value_counts()
            top5_counts = city_counts.head(5)

            print(f"\n🏙️  Top 5 cities:")
            for i, (city, count) in enumerate(top5_counts.items(), 1):
                percentage = (count / len(all_jobs_df)) * 100
                print(f"  {i}. {city}: {count:,} jobs ({percentage:.1f}%)")
    except Exception as e:
        logger.warning(f"Could not compute city summary: {e}")

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

def run_ai_purge_mode(scheduler: TaskScheduler):
    """Run AI-powered job purging using LLM analysis"""
    logger.info("=== Starting AI-Powered Job Purging ===")
    
    try:
        # Initialize AI Purger
        ai_purger = AIPurger(scheduler.config)
        
        # Test LLM connection first
        if not ai_purger.llm_connection():
            logger.error("Failed to connect to LLM - aborting AI purge")
            sys.exit(1)
        
        # Run the AI purge process
        summary = ai_purger.run_purge_mode()
        
        # Log summary
        logger.info(f"AI Purge Summary:")
        logger.info(f"  - Jobs analyzed: {summary['jobs_analyzed']}")
        logger.info(f"  - Batches processed: {summary.get('batches_processed', 0)}")
        logger.info(f"  - Jobs marked for purging: {summary['jobs_to_purge']}")
        logger.info(f"  - Jobs actually purged: {summary['jobs_purged']}")
        
        if summary['success']:
            logger.success("=== AI-Powered Purge Completed Successfully ===")
        else:
            error_msg = summary.get('error', 'Unknown error')
            logger.error(f"=== AI-Powered Purge Failed: {error_msg} ===")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"AI purge mode failed: {e}")
        sys.exit(1)

def run_ai_purge_test_mode(scheduler: TaskScheduler):
    """Run AI-powered job purging in test mode (first batch only, no actual purging)"""
    logger.info("=== Starting AI-Powered Job Purging (TEST MODE) ===")
    
    try:
        # Initialize AI Purger with test mode enabled
        ai_purger = AIPurger(scheduler.config, test_mode=True)
        
        # Test LLM connection first
        if not ai_purger.llm_connection():
            logger.error("Failed to connect to LLM - aborting AI purge test")
            sys.exit(1)
        
        # Run the AI purge process in test mode
        summary = ai_purger.run_purge_mode()
        
        # Log summary
        logger.info(f"AI Purge Test Summary:")
        logger.info(f"  - Mode: TEST MODE (first batch only)")
        logger.info(f"  - Jobs analyzed: {summary['jobs_analyzed']}")
        logger.info(f"  - Batches processed: {summary.get('batches_processed', 0)}")
        logger.info(f"  - Jobs marked for purging: {summary['jobs_to_purge']}")
        logger.info(f"  - Jobs actually purged: {summary['jobs_purged']} (0 in test mode)")
        
        if summary['success']:
            logger.success("=== AI-Powered Purge Test Completed Successfully ===")
            logger.info("=== Review the jobs listed above before running in production mode ===")
        else:
            error_msg = summary.get('error', 'Unknown error')
            logger.error(f"=== AI-Powered Purge Test Failed: {error_msg} ===")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"AI purge test mode failed: {e}")
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

def run_description_parser(scheduler: TaskScheduler):
    """Run incremental description parsing using LLM with caching"""
    logger.info("=== Parsing Descriptions (Incremental) ===")
    success = scheduler.run_description_parser()
    if success:
        logger.success("=== Description Parsing Completed ===")
    else:
        logger.error("=== Description Parsing Failed ===")
        sys.exit(1)

def reset_failed_descriptions(scheduler: TaskScheduler):
    """Reset jobs marked as failed description fetch to allow retrying"""
    logger.info("=== Resetting Failed Description Fetches ===")
    
    # Check current failed count
    failed_count = scheduler.scraper.db.get_failed_description_count()
    if failed_count == 0:
        logger.info("No jobs marked as failed description fetch found.")
        return
    
    logger.info(f"Found {failed_count} jobs marked as failed description fetch")
    
    # Reset them
    reset_count = scheduler.scraper.db.reset_failed_descriptions()
    if reset_count > 0:
        logger.success(f"Successfully reset {reset_count} jobs to allow retrying description fetch")
    else:
        logger.warning("No jobs were reset")

def cleanup_orphaned_descriptions(scheduler: TaskScheduler):
    """Clean up orphaned parsed descriptions that have no corresponding job"""
    logger.info("=== Cleaning Up Orphaned Parsed Descriptions ===")
    
    # Get current stats before cleanup
    summary = scheduler.scraper.db.get_job_summary()
    stats = summary.get('parsed_descriptions_stats', {})
    
    orphaned_count = stats.get('orphaned_parsed_descriptions', 0)
    if orphaned_count == 0:
        logger.info("No orphaned parsed descriptions found.")
        return
    
    logger.info(f"Found {orphaned_count} orphaned parsed descriptions")
    
    # Perform cleanup
    cleaned_count = scheduler.scraper.db.cleanup_orphaned_parsed_descriptions()
    
    if cleaned_count > 0:
        logger.success(f"Successfully cleaned up {cleaned_count} orphaned parsed descriptions")
        
        # Show updated stats
        updated_summary = scheduler.scraper.db.get_job_summary()
        updated_stats = updated_summary.get('parsed_descriptions_stats', {})
        
        print(f"\nUpdated Statistics:")
        print(f"  Total jobs: {updated_summary['total_jobs']:,}")
        print(f"  Jobs with parsed descriptions: {updated_stats.get('jobs_with_descriptions', 0):,}")
        print(f"  Total parsed description entries: {updated_stats.get('total_parsed_descriptions', 0):,}")
        print(f"  Orphaned descriptions remaining: {updated_stats.get('orphaned_parsed_descriptions', 0):,}")
    else:
        logger.warning("No orphaned descriptions were cleaned up")

if __name__ == "__main__":
    main()
