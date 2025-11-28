"""
Agents Module - Specialized task-oriented components

This module contains specialized agents that handle specific job-related tasks:
- JobScraper: Scrapes job postings from various sources
- EmailSender: Handles email notifications and reports
- AIPurger: AI-powered job filtering and purging
- DescriptionTools: LLM-backed job description parsing and analysis
"""

def __getattr__(name):
    """Dynamic import dispatcher to handle circular imports"""
    if name == 'JobScraper':
        from .job_scraper import JobScraper
        return JobScraper
    elif name == 'EmailSender':
        from .email_sender import EmailSender
        return EmailSender
    elif name == 'AIPurger':
        from .ai_purger import AIPurger
        return AIPurger
    elif name == 'DescriptionTools':
        from .parser import DescriptionTools
        return DescriptionTools
    else:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

# Define what's available when using "from src.agents import *"
# Note: These are dynamically imported via __getattr__
__all__ = []

# Version info
__version__ = "1.0.0"