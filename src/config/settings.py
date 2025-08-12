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
    porsche_search_url: str
    linkedin_max_pages: int
    linkedin_desc_max: int
    linkedin_desc_workers: int
    max_total_jobs: int
    min_new_jobs_to_continue: int
    quiet_progress: bool
    
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
    enable_porsche: bool
    dry_run: bool
    # Dry-run tuning
    dry_run_fast: bool
    dry_run_keywords_limit: int
    dry_run_locations_limit: int
    dry_run_pages: int
    dry_run_limit_per_source: int
    dry_run_skip_selenium: bool
    dry_run_request_delay: float
    dry_run_max_retries: int
    dry_run_desc_max: int
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
            email_address=os.getenv('EMAIL_ADDRESS', 'timurozturkk@gmail.com'),
            email_password=os.getenv('EMAIL_PASSWORD', ''),
            recipient_email=os.getenv('RECIPIENT_EMAIL', 'timurozturkk@gmail.com'),
            
            # Job Search Configuration
            search_keywords=os.getenv('SEARCH_KEYWORDS', 'Data Science, Data Analysis, AI'),
            search_locations=os.getenv('SEARCH_LOCATIONS', 'Stuttgart, Berlin, Frankfurt, Köln, Ulm, Konstanz, Zürich, Düsseldorf, Freiburg, München, Augsburg, Nürnberg, Hannover'),
            unwanted_keywords=os.getenv('UNWANTED_KEYWORDS', (
                "manager,ausbildung,hilfskraft,wiss.,Ingenieur,Research assistant,Pflichtpraktikum,Akademische:r,"
                "Studien-/Abschlussarbeit,bachelor,data collection,wissenschaflicher,chair,developer,threat,"
                "mapping,postdoctoral,power bi,hackers,masterthesis,masterarbeit,pharmaberater,abiturientenprogramm,"
                "biologist,customer,logistics,teilzeit,founders,D365,365,MSD365,scientist,founding,client,"
                "nebenberufliche*n,programme,executive,representative,energy,operations,talent,claims,application,"
                "entwicklungsingenieur,creative,sap,test,network,director,researcher,production,product,rwe,support,"
                "teil-,risikocontrolling,coordinator,crm,planner,risikomanagement,programm,abiturientenprogramm,"
                "security,produktionsplaner,supervisor,pharma,paralegal,Sicherheitstechniker,founder,head,"
                "working student,frontend,backend,techniker,manager,planer,nebenberuflich,full stack,lead,dual,"
                "duales,studium,controlling,berater,abitur,praktikum,marketing,verkäufer,internship,sales,"
                "freelance,werkstudent,intern,trainee,thesis,student,part-time,lecturer,tester"
            )),
            
            # Scraping Configuration
            request_delay=float(os.getenv('REQUEST_DELAY', '3.0')),
            max_retries=int(os.getenv('MAX_RETRIES', '2')),
            # Default to a realistic desktop Chrome UA string
            user_agent=os.getenv('USER_AGENT', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'),
            # Third-party sources
            porsche_search_url=os.getenv('PORSCHE_SEARCH_URL', 'https://jobs.porsche.com/index.php?ac=search_result&search_criterion_keyword%5B%5D=Data&search_criterion_channel%5B%5D=12&search_criterion_entry_level%5B%5D=17&search_criterion_entry_level%5B%5D=12&search_criterion_working_hour%5B%5D=1&search_criterion_limitation%5B%5D=2&search_criterion_country%5B%5D=46'),
            linkedin_max_pages=int(os.getenv('LINKEDIN_MAX_PAGES', '3')),
            linkedin_desc_max=int(os.getenv('LINKEDIN_DESC_MAX', '5')),
            linkedin_desc_workers=int(os.getenv('LINKEDIN_DESC_WORKERS', '0')),
            max_total_jobs=int(os.getenv('MAX_TOTAL_JOBS', '0')),
            min_new_jobs_to_continue=int(os.getenv('MIN_NEW_JOBS_TO_CONTINUE', '1')),
            quiet_progress=_get_bool('QUIET_PROGRESS', 'true'),
            
            # Scheduler Configuration
            schedule_time=os.getenv('SCHEDULE_TIME', '09:00'),
            timezone=os.getenv('TIMEZONE', 'Europe/Berlin'),
            
            # Logging Configuration
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
            log_file=os.getenv('LOG_FILE', 'logs/job_informer.log'),
            
            # AI Analysis Configuration
            gemini_api_key=os.getenv('GEMINI_API_KEY', ''),
            gemini_model=os.getenv('GEMINI_MODEL', 'gemini-2.5-flash'),
            gemini_analysis_prompt=os.getenv('GEMINI_ANALYSIS_PROMPT', (
                'You are a concise labor market analyst. Given the job market data summary below, write a one-page (max 400 words) report in plain text. '
                'The report must include:1. Executive summary (3–4 bullet points). 2. Key statistics (total jobs, top companies, top locations, notable job titles, salary insights). '
                '3. Short commentary on trends or anomalies from the period. 4. Brief note on what to watch in the coming period. '
                'Be neutral, data-driven, and avoid speculation. Do not include tables, only bullet points and short paragraphs. '
                'Use only the provided data — no outside sources.'
            )),

            # Feature Toggles
            enable_linkedin=_get_bool('ENABLE_LINKEDIN', 'true'),
            enable_porsche=_get_bool('ENABLE_PORSCHE', 'true'),
            dry_run=_get_bool('DRY_RUN', 'false'),
            # Dry-run tuning (only applied when dry_run is true)
            dry_run_fast=_get_bool('DRY_RUN_FAST', 'true'),
            dry_run_keywords_limit=int(os.getenv('DRY_RUN_KEYWORDS_LIMIT', '1')),
            dry_run_locations_limit=int(os.getenv('DRY_RUN_LOCATIONS_LIMIT', '1')),
            dry_run_pages=int(os.getenv('DRY_RUN_PAGES', '1')),
            dry_run_limit_per_source=int(os.getenv('DRY_RUN_LIMIT_PER_SOURCE', '10')),
            dry_run_skip_selenium=_get_bool('DRY_RUN_SKIP_SELENIUM', 'true'),
            dry_run_request_delay=float(os.getenv('DRY_RUN_REQUEST_DELAY', '0.2')),
            dry_run_max_retries=int(os.getenv('DRY_RUN_MAX_RETRIES', '1')),
            dry_run_desc_max=int(os.getenv('DRY_RUN_DESC_MAX', '0')),
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
            'porsche_search_url': self.porsche_search_url,
            'linkedin_max_pages': self.linkedin_max_pages,
            'linkedin_desc_max': self.linkedin_desc_max,
            'linkedin_desc_workers': self.linkedin_desc_workers,
            'max_total_jobs': self.max_total_jobs,
            'min_new_jobs_to_continue': self.min_new_jobs_to_continue,
            'quiet_progress': self.quiet_progress,
            'schedule_time': self.schedule_time,
            'timezone': self.timezone,
            'log_level': self.log_level,
            'log_file': self.log_file,
            'gemini_api_key': '***HIDDEN***',  # Don't expose API key
            'gemini_model': self.gemini_model,
            'gemini_analysis_prompt': self.gemini_analysis_prompt,
            'enable_linkedin': self.enable_linkedin,
            'enable_porsche': self.enable_porsche,
            'dry_run': self.dry_run,
            'dry_run_fast': self.dry_run_fast,
            'dry_run_keywords_limit': self.dry_run_keywords_limit,
            'dry_run_locations_limit': self.dry_run_locations_limit,
            'dry_run_pages': self.dry_run_pages,
            'dry_run_limit_per_source': self.dry_run_limit_per_source,
            'dry_run_skip_selenium': self.dry_run_skip_selenium,
            'dry_run_request_delay': self.dry_run_request_delay,
            'dry_run_max_retries': self.dry_run_max_retries,
            'dry_run_desc_max': self.dry_run_desc_max,
            'auto_migrate_csv': self.auto_migrate_csv
        }
