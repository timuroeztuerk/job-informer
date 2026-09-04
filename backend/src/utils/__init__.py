"""
Utils Module - Utility functions and classes

This module contains utility functions and classes for:
- Logging utilities
- Data processing utilities  
- Terminal utilities
- General utilities and task management
"""

# Import utility functions and classes
from .logging_utils import setup_logging, get_logger
from .data_utils import (
    clean_job_data,
    extract_salary_info,
    filter_jobs_by_keywords,
    filter_jobs_by_location,
    generate_job_summary
)
from .terminal_utils import (
    _sleep_quiet,
    _sleep_with_feedback,
    _progress_bar,
    _print_progress
)

# Define what's available when using "from src.utils import *"
__all__ = [
    # Logging utilities
    "setup_logging",
    "get_logger",
    
    # Data utilities
    "clean_job_data",
    "extract_salary_info",
    "filter_jobs_by_keywords",
    "filter_jobs_by_location",
    "generate_job_summary",
    
    # Terminal utilities (note: prefixed with underscore, typically internal)
    "_sleep_quiet",
    "_sleep_with_feedback", 
    "_progress_bar",
    "_print_progress"
]
