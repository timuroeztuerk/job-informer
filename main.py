#!/usr/bin/env python3
"""
Job Informer - Main Application Entry Point
Automated job posting monitoring and notification system
"""

import argparse
import sys
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd
from loguru import logger


# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.config.settings import Config
from src.agents.job_scraper import JobScraper
from src.agents.parser import DescriptionTools
from src.agents.email_sender import EmailSender
from src.agents.ai_purger import AIPurger
from src.utils.logging_utils import setup_logging
from src.utils.database import JobDatabase

def create_data_directory():
    """Create data directory if it doesn't exist"""
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    return data_dir


CITY_ALIASES = {
    "berlin metropolitan area": "Berlin",
    "berlin area": "Berlin",
    "cologne": "Cologne",
    "koeln": "Cologne",
    "koln": "Cologne",
    "dusseldorf": "Düsseldorf",
    "duesseldorf": "Düsseldorf",
    "frankfurt": "Frankfurt am Main",
    "frankfurt am main": "Frankfurt am Main",
    "munchen": "Munich",
    "muenchen": "Munich",
    "munich": "Munich",
    "remote": "Remote",
    "remote germany": "Remote",
    "remote - germany": "Remote",
    "zurich": "Zurich",
    "zurich area": "Zurich",
    "zuerich": "Zurich",
}


def normalize_city_name(city: str) -> str:
    """Normalize city labels to reduce duplicates caused by casing or accents."""
    if city is None:
        return "Unknown"

    city_clean = " ".join(str(city).strip().split())
    if not city_clean:
        return "Unknown"

    lower_clean = city_clean.lower()
    if lower_clean in {"nan", "none", "null"}:
        return "Unknown"

    ascii_key = unicodedata.normalize("NFKD", city_clean).encode("ascii", "ignore").decode("ascii") or city_clean
    ascii_key_lower = ascii_key.lower()

    alias = CITY_ALIASES.get(ascii_key_lower) or CITY_ALIASES.get(lower_clean)
    if alias:
        return alias

    if ascii_key_lower.startswith("remote") or lower_clean.startswith("remote"):
        return "Remote"

    return city_clean.title()


def extract_primary_city(location: str) -> str:
    """Return normalized primary city extracted from a full location string."""
    if location is None:
        return "Unknown"

    primary = str(location).split(",")[0]
    return normalize_city_name(primary)


DEGREE_FIELD_ALIASES = {
    "computer science": "computer science",
    "informatics": "computer science",
    "cs": "computer science",
    "data science": "data science",
    "ai": "data science",
    "artificial intelligence": "data science",
    "machine learning": "data science",
    "engineering": "engineering",
    "electrical engineering": "engineering",
    "software engineering": "engineering",
    "statistics": "statistics",
    "mathematics": "mathematics",
    "math": "mathematics",
    "economics": "economics",
    "business administration": "business administration",
    "finance": "finance",
    "related field": "related field",
    "related fields": "related field",
    "bwl": "business administration",
    "wirtschaftsinformatik": "business informatics",
    "datenwissenschaften": "data science",
    "psychology": "psychology",
    "cognitive science": "cognitive science",
    "linguistics": "linguistics",
    "robotics": "robotics",
}


def normalize_degree_fields(value: str) -> list[str]:
    """Return a list of normalized degree fields derived from an LLM payload value."""
    if value is None:
        return ["unspecified"]

    if isinstance(value, list):
        candidates = value
    else:
        # Split on common separators (|, /, commas)
        candidates = re.split(r"[|/,]", str(value))

    normalized: list[str] = []
    for candidate in candidates:
        clean = " ".join(candidate.strip().split()).lower()
        if not clean or clean in {"", "null", "none"}:
            continue
        if clean in {"unspecified", "not specified"}:
            normalized.append("unspecified")
            continue
        alias = DEGREE_FIELD_ALIASES.get(clean, clean)
        normalized.append(alias)

    if not normalized:
        return ["unspecified"]

    # Preserve insertion order while removing duplicates
    seen = set()
    unique = []
    for item in normalized:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique

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
    group.add_argument('--purge', action='store_true', help='Purge unwanted jobs')
    group.add_argument('--ai-purge', dest='ai_purge', action='store_true', help='Run AI-powered job purging')
    group.add_argument('--get-descriptions', dest='get_descriptions', action='store_true', help='Backfill missing job descriptions')
    group.add_argument('--parse-descriptions', dest='parse_descriptions', action='store_true', help='Parse job descriptions with AI')
    group.add_argument('--reset-ai-purge', dest='reset_ai_purge', action='store_true', help='Reset AI purge analysis flags')
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

    # Start with default logging so early errors are visible
    setup_logging()
    # Default to run-once if no mode flag is set
    if not any([args.run_once, args.test, args.db_summary, args.purge, args.ai_purge, args.get_descriptions, args.parse_descriptions, args.reset_ai_purge, args.inform]):
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
        mode_str = 'ai-purge'
    elif args.inform:
        mode_str = 'inform'
    elif args.get_descriptions:
        mode_str = 'get-descriptions'
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
            purge_unwanted_jobs(config)
        elif args.ai_purge:
            run_ai_purge_mode(config)
        elif args.inform:
            run_inform_mode(config, args.inform)
        elif args.get_descriptions:
            get_descriptions(config)
        elif args.parse_descriptions:
            run_description_parser(DescriptionTools(config))
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
    summary = scraper.db.get_job_summary()
    print(f"\n📊 Job Database Summary:")
    print(f"Total jobs: {summary['total_jobs']:,}")
    print(f"Recent jobs (7 days): {summary['recent_jobs_7_days']:,}")
    print(f"Date range: {summary['date_range']['earliest']} to {summary['date_range']['latest']}")
    # Add parsed description analytics
    try:
        with scraper.db._get_connection() as conn:
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
                        if 'degree_field' in data:
                            for field in normalize_degree_fields(data['degree_field']):
                                degree_fields[field] = degree_fields.get(field, 0) + 1
                        
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
                    filtered_degree_fields = [(field, count) for field, count in sorted_degree_fields if field != "unspecified"]
                    display_degree_fields = filtered_degree_fields or sorted_degree_fields
                    print(f"\n📜 Degree Fields:")
                    for field, count in display_degree_fields[:5]:
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
                print(f"\n🧠 No parsed descriptions found - run 'python main.py --parse-descriptions' first")
                
    except Exception as e:
        logger.warning(f"Could not compute parsed descriptions analytics: {e}")
    
    # City/description coverage breakdown
    try:
        with scraper.db._get_connection() as conn:
            all_jobs_df = pd.read_sql_query("SELECT location, description FROM jobs", conn)
        
        if all_jobs_df is None or all_jobs_df.empty:
            print(f"\n==== Top 5 Cities ====\nNo jobs to summarize.\n")
        else:
            city_series = all_jobs_df['location'].apply(extract_primary_city)
            city_counts = city_series.value_counts()
            top5_counts = city_counts.head(5)
            total_entries = len(city_series)

            print(f"\n🏙️  Top 5 cities:")
            for i, (city, count) in enumerate(top5_counts.items(), 1):
                percentage = (count / total_entries) * 100 if total_entries else 0.0
                print(f"  {i}. {city}: {count:,} jobs ({percentage:.1f}%)")
    except Exception as e:
        logger.warning(f"Could not compute city summary: {e}")

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

def get_descriptions(config: Config):
    """Backfill missing job descriptions in the database"""
    desc = DescriptionTools(config)
    scraper = JobScraper(config)
    success = desc.backfill_missing_descriptions(scraper)
    if success:
        logger.success("=== Done ===")
    else:
        logger.error("=== Backfill Failed ===")
        sys.exit(1)

def run_description_parser(desc: DescriptionTools):
    """Run incremental description parsing using LLM with caching"""
    success = desc.run_description_parser()
    if success:
        logger.success("=== Done ===")
    else:
        logger.error("=== Description Parsing Failed ===")
        sys.exit(1)

if __name__ == "__main__":
    main()
