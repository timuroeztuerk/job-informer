# Version information
__version__ = "1.0.1"

# Delayed imports to avoid circular dependencies
def get_job_scraper():
    from .scrapers.job_scraper import JobScraper
    return JobScraper

def get_utilities():
    from .utils.utilities import Utilities
    return Utilities

def get_email_sender():
    from .email_notifier.email_sender import EmailSender
    return EmailSender

# Empty __all__ to discourage direct imports from this package
__all__ = []