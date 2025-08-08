"""
Configuration Module
Handles application settings and environment variables
"""

import os
from dotenv import load_dotenv
from typing import Optional
from dataclasses import dataclass
import re


@dataclass
class Config:
    """Configuration class for Job Informer application"""
    
    # Email Configuration
    smtp_server: str
    smtp_port: int
    email_address: str
    email_password: str
    recipient_email: str
    
    # Job Search Configuration
    search_keywords: str
    search_locations: str
    unwanted_keywords: str
    
    # Scraping Configuration
    request_delay: float
    max_retries: int
    user_agent: str
    
    # Scheduler Configuration
    schedule_time: str
    timezone: str
    
    # Logging Configuration
    log_level: str
    log_file: str
    
    # AI Analysis Configuration
    gemini_api_key: str
    gemini_model: str
    gemini_analysis_prompt: str

    # Feature Toggles
    enable_linkedin: bool
    enable_indeed: bool
    dry_run: bool
    auto_migrate_csv: bool
    
    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> 'Config':
        """Load configuration from environment variables"""
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()  # Load from .env file in current directory
        
        def _get_bool(name: str, default: str = 'false') -> bool:
            value = os.getenv(name, default)
            return bool(re.match(r"^(1|true|yes|y|on)$", str(value).strip(), re.IGNORECASE))

        return cls(
            # Email Configuration
            smtp_server=os.getenv('SMTP_SERVER', 'smtp.gmail.com'),
            smtp_port=int(os.getenv('SMTP_PORT', '587')),
            email_address=os.getenv('EMAIL_ADDRESS', ''),
            email_password=os.getenv('EMAIL_PASSWORD', ''),
            recipient_email=os.getenv('RECIPIENT_EMAIL', ''),
            
            # Job Search Configuration
            search_keywords=os.getenv('SEARCH_KEYWORDS', 'python developer,software engineer'),
            search_locations=os.getenv('SEARCH_LOCATIONS', 'New York,San Francisco,Remote'),
            unwanted_keywords=os.getenv('UNWANTED_KEYWORDS', 'internship,sales,freelance,intern,trainee,thesis,student,part-time,lecturer,tester,manager'),
            
            # Scraping Configuration
            request_delay=float(os.getenv('REQUEST_DELAY', '2.0')),
            max_retries=int(os.getenv('MAX_RETRIES', '3')),
            user_agent=os.getenv('USER_AGENT', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'),
            
            # Scheduler Configuration
            schedule_time=os.getenv('SCHEDULE_TIME', '09:00'),
            timezone=os.getenv('TIMEZONE', 'America/New_York'),
            
            # Logging Configuration
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
            log_file=os.getenv('LOG_FILE', 'logs/job_informer.log'),
            
            # AI Analysis Configuration
            gemini_api_key=os.getenv('GEMINI_API_KEY', ''),
            gemini_model=os.getenv('GEMINI_MODEL', 'gemini-2.0-flash-exp'),
            gemini_analysis_prompt=os.getenv('GEMINI_ANALYSIS_PROMPT', 'Analyze the following job market data and provide comprehensive insights.'),

            # Feature Toggles
            enable_linkedin=_get_bool('ENABLE_LINKEDIN', 'true'),
            enable_indeed=_get_bool('ENABLE_INDEED', 'false'),
            dry_run=_get_bool('DRY_RUN', 'false'),
            auto_migrate_csv=_get_bool('AUTO_MIGRATE_CSV', 'false')
        )
    
    def validate_for_mode(self, mode: str) -> None:
        """Validate configuration values depending on execution mode"""
        errors = []

        # Common validations
        if self.request_delay < 0:
            errors.append("REQUEST_DELAY must be positive")
        if self.max_retries < 0:
            errors.append("MAX_RETRIES must be positive")

        # Modes that require email configuration
        email_required_modes = {
            'run-once', 'test', 'summary', 'market-report', 'schedule'
        }
        # Modes that require Gemini
        gemini_required_modes = {'market-report', 'test'}

        if mode in email_required_modes:
            if not self.email_address:
                errors.append("EMAIL_ADDRESS is required")
            if not self.email_password:
                errors.append("EMAIL_PASSWORD is required")
            if not self.recipient_email:
                errors.append("RECIPIENT_EMAIL is required")

        if mode in {'run-once', 'schedule'}:
            if not self.search_keywords:
                errors.append("SEARCH_KEYWORDS is required")
            if not self.search_locations:
                errors.append("SEARCH_LOCATIONS is required")

        if mode in gemini_required_modes:
            if not self.gemini_api_key:
                errors.append("GEMINI_API_KEY is required for AI analysis features")

        if errors:
            raise ValueError(f"Configuration validation failed: {', '.join(errors)}")

    # Backward compatibility
    def validate(self) -> None:
        self.validate_for_mode('run-once')
    
    def get_keywords_list(self) -> list:
        """Get search keywords as a list"""
        return [k.strip() for k in self.search_keywords.split(',') if k.strip()]
    
    def get_locations_list(self) -> list:
        """Get search locations as a list"""
        return [l.strip() for l in self.search_locations.split(',') if l.strip()]
    
    def get_unwanted_keywords_list(self) -> list:
        """Get unwanted keywords as a list"""
        return [k.strip().lower() for k in self.unwanted_keywords.split(',') if k.strip()]
    
    def to_dict(self) -> dict:
        """Convert configuration to dictionary"""
        return {
            'smtp_server': self.smtp_server,
            'smtp_port': self.smtp_port,
            'email_address': self.email_address,
            'email_password': '***HIDDEN***',  # Don't expose password
            'recipient_email': self.recipient_email,
            'search_keywords': self.search_keywords,
            'search_locations': self.search_locations,
            'unwanted_keywords': self.unwanted_keywords,
            'request_delay': self.request_delay,
            'max_retries': self.max_retries,
            'user_agent': self.user_agent,
            'schedule_time': self.schedule_time,
            'timezone': self.timezone,
            'log_level': self.log_level,
            'log_file': self.log_file,
            'gemini_api_key': '***HIDDEN***',  # Don't expose API key
            'gemini_model': self.gemini_model,
            'gemini_analysis_prompt': self.gemini_analysis_prompt,
            'enable_linkedin': self.enable_linkedin,
            'enable_indeed': self.enable_indeed,
            'dry_run': self.dry_run,
            'auto_migrate_csv': self.auto_migrate_csv
        }
