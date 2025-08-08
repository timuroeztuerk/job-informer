#!/usr/bin/env python3
"""
Job Informer - Main Application Entry Point
Automated job posting monitoring and notification system
"""

import argparse
import sys
import os
from pathlib import Path

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
        choices=['run-once', 'test-email', 'quick-test', 'summary-full', 'summary-latest', 'filter-historical', 'market-report', 'test-ai'],
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
        if args.schedule_time:
            config.schedule_time = args.schedule_time
        
        # Validate configuration
        config.validate()
        
        # Setup logging
        setup_logging(config.log_level, config.log_file)
        
        logger.info("Starting Job Informer application")
        logger.info(f"Mode: {args.mode}")
        
        # Create data directory
        create_data_directory()
        
        # Execute based on mode
        scheduler = TaskScheduler(config)
        
        if args.mode == 'test-email':
            test_email_configuration(scheduler)
        elif args.mode == 'run-once':
            run_job_search_once(scheduler)
        elif args.mode == 'quick-test':
            run_quick_test(scheduler)
        elif args.mode == 'summary-full':
            run_full_summary(scheduler)
        elif args.mode == 'summary-latest':
            run_latest_summary(scheduler)
        elif args.mode == 'filter-historical':
            filter_historical_jobs(scheduler)
        elif args.mode == 'market-report':
            generate_market_report(scheduler)
        elif args.mode == 'test-ai':
            test_ai_connection(scheduler)
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


def filter_historical_jobs(scheduler: TaskScheduler):
    """Filter historical jobs and send filtered results"""
    logger.info("=== Filtering Historical Jobs ===")
    
    success = scheduler.filter_and_send_historical_jobs()
    
    if success:
        logger.success("=== Filtered Historical Jobs Sent Successfully ===")
    else:
        logger.error("=== Historical Jobs Filtering Failed ===")
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


if __name__ == "__main__":
    main()
