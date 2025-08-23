"""
Logging Utilities
Setup and configuration for application logging
"""
import os
import sys
from loguru import logger
from typing import Optional

def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Setup logging configuration"""
    
    # Remove default logger
    logger.remove()
    
    # Add console logging with colors
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=log_level,
        colorize=True
    )
    
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

def get_logger(name: str):
    """Get logger instance for a specific module"""
    return logger.bind(name=name)