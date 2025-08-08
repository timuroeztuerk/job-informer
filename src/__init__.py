from .scrapers.job_scraper import JobScraper
from .scheduler.task_scheduler import TaskScheduler
from .email_notifier.email_sender import EmailSender
from .analyst.market_analyst import MarketAnalyst

__version__ = "1.0.0"
__all__ = ["JobScraper", "TaskScheduler", "EmailSender", "MarketAnalyst"]
