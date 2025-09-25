"""
Job Informer Package

Main package for the Job Informer automated job monitoring system.
Provides access to configuration, utilities, and agent components.
"""

# Version information
__version__ = "1.0.1"

# Delayed imports to avoid circular dependencies
def get_job_scraper():
    """Get JobScraper class (deferred import)"""
    from .agents.job_scraper import JobScraper
    return JobScraper


def get_email_sender():
    """Get EmailSender class (deferred import)"""
    from .agents.email_sender import EmailSender
    return EmailSender

def get_config():
    """Get Config class (deferred import)"""
    from .config.settings import Config
    return Config

# Expose deferred imports for convenience
__all__ = [
    "get_job_scraper",
    "get_email_sender",
    "get_config",
    "__version__"
]