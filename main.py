#!/usr/bin/env python3
"""
Job Informer - Main Application Entry Point
Automated job posting monitoring and notification system
"""

import os
import argparse
import sys
import json
from pathlib import Path
import pandas as pd

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.config.settings import Config
from src.utils.logging_utils import setup_logging
from src.scrapers.job_scraper import JobScraper
from src.description_tools import DescriptionTools
from src.utils.utilities import Utilities
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

    args = parser.parse_args()
    # Default to run-once if no mode flag is set
    if not any([args.run_once, args.test, args.db_summary, args.purge, args.ai_purge, args.get_descriptions, args.parse_descriptions]):
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
    elif args.get_descriptions:
        mode_str = 'get-descriptions'
    elif args.parse_descriptions:
        mode_str = 'parse-descriptions'
    else:
        mode_str = 'run-once'
    
    try:
        # Load configuration
        config = Config.from_env(args.config)
        # Override config with command line arguments if provided
        if args.keywords:
            config.search_keywords = args.keywords
        if args.locations:
            config.search_locations = args.locations
        # Validate configuration for the selected mode
        config.validate_for_mode(mode_str)
        # Setup logging
        setup_logging(config.log_level, config.log_file)
        logger.info(f"Mode: {mode_str}")
        create_data_directory()
        
        if args.run_once:
            run_job_search_once(Utilities(config))  
        elif args.test:
            run_all_tests(Utilities(config))
        elif args.db_summary:
            show_database_summary(Utilities(config))
        elif args.purge:
            purge_unwanted_jobs(Utilities(config))
        elif args.ai_purge:
            run_ai_purge_mode(Utilities(config))
        elif args.get_descriptions:
            get_descriptions(DescriptionTools(config), Utilities(config), JobScraper(config))
        elif args.parse_descriptions:
            run_description_parser(DescriptionTools(config))
        else:
            logger.error(f"Unsupported mode: {mode_str}")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Application error: {e}")
        sys.exit(1)

def run_job_search_once(utils: Utilities):
    """Run the Job Scraper"""
    summary = utils.get_search_summary()
    logger.info(f"Starting, Keywords: {', '.join(summary['keywords'])}, Locations: {', '.join(summary['locations'])}")
    
    success = utils.execute_job_search()
    if success:
        logger.success("=== Job Search Done ===")
    else:
        logger.warning("=== No Jobs Found ===")

def run_all_tests(utils: Utilities):
    """Run combined tests: config/email test, scraper init, and AI connection"""
    overall_success = True
    
    try:
        utils.config.validate()
        logger.success("Configuration validation passed")
        try:
            success = utils.email_sender.send_test_email()
            if success:
                logger.success("Email test successful! Check your inbox.")
            else:
                logger.error("Email test failed!")
                overall_success = False
        except Exception as e:
            logger.error(f"Email test error: {e}")
            overall_success = False
        if utils.scraper:
            logger.success("Scraper initialized successfully")
        else:
            logger.error("Scraper initialization failed")
            overall_success = False
        
        # Test LLM connection (if API key is configured)
        try:
            if hasattr(utils.config, 'gemini_api_key') and utils.config.gemini_api_key:
                # Initialize AI Purger with test mode enabled
                ai_purger = AIPurger(utils.config, test_mode=True)
                # Test LLM connection
                if ai_purger.llm_connection():
                    logger.success("LLM connection successful")
                else:
                    logger.error("AI LLM connection failed")
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

def show_database_summary(utils: Utilities):
    """Show database summary statistics"""
    logger.info("=== Database Summary ===")
    summary = utils.scraper.db.get_job_summary()
    print(f"\n📊 Job Database Summary:")
    print(f"Total jobs: {summary['total_jobs']:,}")
    print(f"Recent jobs (7 days): {summary['recent_jobs_7_days']:,}")
    print(f"Date range: {summary['date_range']['earliest']} to {summary['date_range']['latest']}")
    # Add parsed description analytics
    try:
        with utils.scraper.db._get_connection() as conn:
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
                print(f"\n🧠 No parsed descriptions found - run 'python main.py --parse-descriptions' first")
                
    except Exception as e:
        logger.warning(f"Could not compute parsed descriptions analytics: {e}")
    
    # City/description coverage breakdown
    try:
        with utils.scraper.db._get_connection() as conn:
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

def purge_unwanted_jobs(utils: Utilities):
    """Purge unwanted jobs from the database based on current filters"""
    logger.info("=== Purging Unwanted Jobs from Database ===")
    success = utils.purge_unwanted_jobs()
    
    if success:
        logger.success("=== Database Purge Completed Successfully ===")
    else:
        logger.error("=== Database Purge Failed ===")
        sys.exit(1)

def run_ai_purge_mode(utils: Utilities):
    """Run AI-powered job purging using LLM analysis"""
    logger.info("=== Starting AI-Powered Job Purging ===")
    
    try:
        # Initialize AI Purger
        ai_purger = AIPurger(utils.config)
        
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

def get_descriptions(desc: DescriptionTools, utils: Utilities, scraper: JobScraper):
    """Backfill missing job descriptions in the database"""
    logger.info("=== Backfilling Missing Job Descriptions ===")
    success = desc.backfill_missing_descriptions(utils, scraper)
    if success:
        logger.success("=== Backfill Completed Successfully ===")
    else:
        logger.error("=== Backfill Failed ===")
        sys.exit(1)

def run_description_parser(desc: DescriptionTools):
    """Run incremental description parsing using LLM with caching"""
    logger.info("=== Parsing Descriptions (Incremental) ===")
    success = desc.run_description_parser()
    if success:
        logger.success("=== Description Parsing Completed ===")
    else:
        logger.error("=== Description Parsing Failed ===")
        sys.exit(1)

if __name__ == "__main__":
    main()
