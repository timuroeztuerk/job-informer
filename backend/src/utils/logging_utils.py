"""
Logging Utilities
Setup and configuration for application logging
"""
import os
import sys
from loguru import logger
from typing import Optional
import logging

class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except Exception:
            level = "INFO"
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())
    

def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Setup logging configuration"""
    
    logger.remove()
    logger.add(
        sys.stdout,
        colorize=sys.stdout.isatty(),
        format="<level>{level.icon}</level> <cyan>{message}</cyan>",
        level="INFO",
    )
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    
    # Add file logging if specified
    if log_file:
        # Create logs directory if it doesn't exist
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        logger.add(
            log_file,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level=log_level,
            rotation="10 MB",  # Rotate when file size reaches 10MB
            retention="30 days",  # Keep logs for 30 days
            compression="zip"  # Compress old logs
        )
