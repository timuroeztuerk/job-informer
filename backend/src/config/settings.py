"""
Configuration Module
Handles application settings and environment variables
"""

import os
from dotenv import dotenv_values, load_dotenv
from typing import Optional
from dataclasses import dataclass
from pathlib import Path
import re


VALID_TIME_RANGES = {"day", "week", "month"}
DEFAULT_TIME_RANGE = "day"
BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LOG_FILE = str(BASE_DIR / "logs" / "job_informer.log")
DEFAULT_JOBS_DB_PATH = str(BASE_DIR / "data" / "jobs.db")
DEFAULT_UNWANTED_KEYWORDS = (
    "intern,internship,praktikant,praktikum,werkstudent,working student,"
    "student assistant,student helper,studentenjob,thesis,masterarbeit,masterthesis,"
    "bachelorarbeit,bachelorthesis,dissertation,doctoral,phd student,trainee,traineeship,"
    "ausbildung,duales studium,research assistant,hiwi,engineer,engineering,"
    "fachkraft,fachinformatiker,research,postdoctoral,software,entwickl,"
    "projektleiter,informatik,backend,frontend"
)


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
    search_time_range: str
    unwanted_keywords: str
    unwanted_companies: str
    
    # Scraping Configuration
    request_delay: float
    max_retries: int
    user_agent: str
    max_total_jobs: int
    min_new_jobs_to_continue: int
    quiet_progress: bool
    
    # Logging Configuration
    log_level: str
    log_file: str

    # Data Configuration
    jobs_db_path: str
    
    # AI Analysis Configuration
    openai_api_key: str
    openai_model: str
    ai_purge_min_confidence: float
    ai_purge_max_ratio: float
    ai_purge_max_jobs: int
    llm_timeout_seconds: float
    llm_max_retries: int
    llm_retry_base_delay: float
    llm_retry_max_delay: float
    llm_retry_jitter: float
    
    # Description Parser Configuration
    enable_description_parser: bool
    desc_parser_model: str
    desc_parser_prompt: str
    desc_parser_batch_size: int
    desc_parser_max_batches: int
    desc_parser_version: int
    desc_parser_min_chars: int
    desc_parser_max_chars: int
    desc_parser_dry_run: bool
    desc_parser_concurrency: int
    scrape_descriptions_on_search: bool
    scrape_descriptions_limit: int

    # Feature Toggles
    enable_linkedin: bool
    enable_indeed: bool
    linkedin_max_search_pages: int
    indeed_max_search_pages: int
    indeed_session_cookies: str
    dry_run: bool
    auto_purge_before_scraping: bool
    
    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> 'Config':
        """Load configuration from environment variables"""
        loaded_env_path: Path | None = None
        explicit_jobs_db_path: str | None = None
        if env_file:
            loaded_env_path = Path(env_file).expanduser().resolve()
            explicit_values = dotenv_values(loaded_env_path)
            if "JOBS_DB_PATH" in explicit_values:
                explicit_jobs_db_path = str(explicit_values.get("JOBS_DB_PATH") or "").strip()
            load_dotenv(loaded_env_path)
        else:
            default_env = BASE_DIR / ".env"
            fallback_env = BASE_DIR.parent / ".env"
            if default_env.exists():
                loaded_env_path = default_env.resolve()
                load_dotenv(default_env)
            elif fallback_env.exists():
                loaded_env_path = fallback_env.resolve()
                load_dotenv(fallback_env)
            else:
                load_dotenv()  # Fallback to current directory

        raw_jobs_db_path = (
            explicit_jobs_db_path
            if explicit_jobs_db_path is not None
            else os.getenv('JOBS_DB_PATH', '').strip()
        )
        if raw_jobs_db_path:
            configured_db_path = Path(raw_jobs_db_path).expanduser()
            if not configured_db_path.is_absolute():
                base_dir = loaded_env_path.parent if loaded_env_path else BASE_DIR.parent
                configured_db_path = base_dir / configured_db_path
            jobs_db_path = str(configured_db_path.resolve())
        else:
            jobs_db_path = str(Path(DEFAULT_JOBS_DB_PATH).resolve())

        # Components construct JobDatabase independently; normalize the shared
        # environment value once so every component selects the same file.
        os.environ['JOBS_DB_PATH'] = jobs_db_path
        
        def _get_bool(name: str, default: str = 'false') -> bool:
            value = os.getenv(name, default)
            return bool(re.match(r"^(1|true|yes|y|on)$", str(value).strip(), re.IGNORECASE))

        def _get_time_range(name: str, default: str = DEFAULT_TIME_RANGE) -> str:
            value = os.getenv(name, default)
            value = (value or default).strip().lower()
            if value not in VALID_TIME_RANGES:
                return DEFAULT_TIME_RANGE
            return value

        def _get_float(name: str, default: str) -> float:
            try:
                return float(os.getenv(name, default))
            except (TypeError, ValueError):
                return float(default)

        def _get_int(name: str, default: str) -> int:
            try:
                return int(os.getenv(name, default))
            except (TypeError, ValueError):
                return int(default)

        return cls(
            # Email Configuration
            smtp_server=os.getenv('SMTP_SERVER', 'smtp.gmail.com'),
            smtp_port=int(os.getenv('SMTP_PORT', '587')),
            email_address=os.getenv('EMAIL_ADDRESS', ''),
            email_password=os.getenv('EMAIL_PASSWORD', ''),
            recipient_email=os.getenv('RECIPIENT_EMAIL', ''),
            
            # Job Search Configuration
            search_keywords=os.getenv('SEARCH_KEYWORDS', 'Data Scientist, Data Analyst, AI Engineer'),
            search_locations=os.getenv('SEARCH_LOCATIONS', 'Stuttgart, Berlin, Frankfurt, Köln, Ulm, Konstanz, Zürich, Düsseldorf, Freiburg, München, Augsburg, Nürnberg, Hannover'),
            search_time_range=_get_time_range('SEARCH_TIME_RANGE', DEFAULT_TIME_RANGE),
            unwanted_keywords=os.getenv('UNWANTED_KEYWORDS', DEFAULT_UNWANTED_KEYWORDS),
            unwanted_companies=os.getenv('UNWANTED_COMPANIES', ''),
            
            # Scraping Configuration
            request_delay=float(os.getenv('REQUEST_DELAY', '2.0')),
            max_retries=int(os.getenv('MAX_RETRIES', '2')),
            # Default to a realistic desktop Chrome UA string
            user_agent=os.getenv('USER_AGENT', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'),
            max_total_jobs=int(os.getenv('MAX_TOTAL_JOBS', '0')),
            min_new_jobs_to_continue=int(os.getenv('MIN_NEW_JOBS_TO_CONTINUE', '1')),
            quiet_progress=_get_bool('QUIET_PROGRESS', 'true'),
            linkedin_max_search_pages=int(os.getenv('LINKEDIN_MAX_SEARCH_PAGES', '0')),
            indeed_max_search_pages=int(os.getenv('INDEED_MAX_SEARCH_PAGES', '0')),
            indeed_session_cookies=os.getenv('INDEED_SESSION_COOKIES', ''),
            
            # Logging Configuration
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
            log_file=os.getenv('LOG_FILE', DEFAULT_LOG_FILE),

            # Data Configuration
            jobs_db_path=jobs_db_path,
            
            # AI Analysis Configuration
            openai_api_key=os.getenv('OPENAI_API_KEY', ''),
            openai_model=os.getenv('OPENAI_MODEL', 'gpt-5-mini'),
            ai_purge_min_confidence=_get_float('AI_PURGE_MIN_CONFIDENCE', '0.75'),
            ai_purge_max_ratio=_get_float('AI_PURGE_MAX_RATIO', '0.35'),
            ai_purge_max_jobs=_get_int('AI_PURGE_MAX_JOBS', '0'),
            llm_timeout_seconds=_get_float('LLM_TIMEOUT_SECONDS', '45'),
            llm_max_retries=_get_int('LLM_MAX_RETRIES', '3'),
            llm_retry_base_delay=_get_float('LLM_RETRY_BASE_DELAY', '0.75'),
            llm_retry_max_delay=_get_float('LLM_RETRY_MAX_DELAY', '8.0'),
            llm_retry_jitter=_get_float('LLM_RETRY_JITTER', '0.2'),
            
            # Description Parser Configuration
            enable_description_parser=_get_bool('ENABLE_DESCRIPTION_PARSER', 'true'),
            desc_parser_model=os.getenv('DESC_PARSER_MODEL', os.getenv('OPENAI_MODEL', 'gpt-5-mini')),
            desc_parser_prompt=os.getenv('DESC_PARSER_PROMPT', (
                'Extract structured job information from the provided title, company, and description.\n'
                'Use only the provided text. Do not infer beyond what is reasonably stated.\n'
                'If unknown: use "unspecified" for enum/string placeholders, null for numeric values, and [] for list fields.\n'
                'Keep languages, programming languages, tools, skills, locations, and extra benefits concise and deduplicated.\n'
                'Interpret salary_eur_range as annual gross EUR only when explicitly stated; otherwise leave min and max null.\n'
                'Keep the summary factual and short: 1-2 sentences, max 30 words.'
            )),
            desc_parser_batch_size=int(os.getenv('DESC_PARSER_BATCH_SIZE', '25')),
            desc_parser_max_batches=int(os.getenv('DESC_PARSER_MAX_BATCHES', '10')),
            desc_parser_version=int(os.getenv('DESC_PARSER_VERSION', '1')),
            desc_parser_min_chars=_get_int('DESC_PARSER_MIN_CHARS', '80'),
            desc_parser_max_chars=_get_int('DESC_PARSER_MAX_CHARS', '12000'),
            desc_parser_dry_run=_get_bool('DESC_PARSER_DRY_RUN', 'false'),
            desc_parser_concurrency=int(os.getenv('DESC_PARSER_CONCURRENCY', '25')),
            scrape_descriptions_on_search=_get_bool('SCRAPE_DESCRIPTIONS', 'true'),
            scrape_descriptions_limit=int(os.getenv('SCRAPE_DESCRIPTIONS_LIMIT', '20')),

            # Feature Toggles
            enable_linkedin=_get_bool('ENABLE_LINKEDIN', 'true'),
            enable_indeed=_get_bool('ENABLE_INDEED', 'false'),
            dry_run=_get_bool('DRY_RUN', 'false'),
            auto_purge_before_scraping=_get_bool('AUTO_PURGE_BEFORE_SCRAPING', 'true')
        )
    
    def validate_for_mode(self, mode: str) -> None:
        """Validate configuration values depending on execution mode"""
        errors = []

        # Modes that require email configuration
        email_required_modes = {
            'test',
            'inform',
        }
        # Modes that require LLM access
        ai_required_modes = {'test', 'parse-descriptions', 'purge'}

        if mode in email_required_modes:
            if not self.email_address:
                errors.append("EMAIL_ADDRESS is required")
            if not self.email_password:
                errors.append("EMAIL_PASSWORD is required")
            if not self.recipient_email:
                errors.append("RECIPIENT_EMAIL is required")

        if mode in {'run-once'}:
            if not self.search_keywords:
                errors.append("SEARCH_KEYWORDS is required")
            if not self.search_locations:
                errors.append("SEARCH_LOCATIONS is required")
            if (self.search_time_range or '').strip().lower() not in VALID_TIME_RANGES:
                errors.append("SEARCH_TIME_RANGE must be one of: day, week, month")

        if mode in ai_required_modes:
            if not self.openai_api_key:
                errors.append("OPENAI_API_KEY is required for AI analysis features")

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
        """Get unwanted keywords as a list with normalized encoding"""
        import unicodedata
        keywords = [k.strip().lower() for k in self.unwanted_keywords.split(',') if k.strip()]
        # Normalize unicode characters for better matching
        normalized_keywords = []
        for kw in keywords:
            normalized = unicodedata.normalize('NFD', kw).encode('ascii', 'ignore').decode('ascii')
            normalized_keywords.append(normalized)
        return normalized_keywords
    
    def get_unwanted_companies_list(self) -> list:
        """Get unwanted companies as a list with normalized encoding"""
        import unicodedata
        companies = [c.strip().lower() for c in self.unwanted_companies.split(',') if c.strip()]
        # Normalize unicode characters for better matching  
        normalized_companies = []
        for company in companies:
            normalized = unicodedata.normalize('NFD', company).encode('ascii', 'ignore').decode('ascii')
            normalized_companies.append(normalized)
        return normalized_companies
    
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
            'search_time_range': self.search_time_range,
            'unwanted_keywords': self.unwanted_keywords,
            'unwanted_companies': self.unwanted_companies,
            'request_delay': self.request_delay,
            'max_retries': self.max_retries,
            'user_agent': self.user_agent,
            'max_total_jobs': self.max_total_jobs,
            'min_new_jobs_to_continue': self.min_new_jobs_to_continue,
            'quiet_progress': self.quiet_progress,
            'log_level': self.log_level,
            'log_file': self.log_file,
            'openai_api_key': '***HIDDEN***',  # Don't expose API key
            'openai_model': self.openai_model,
            'ai_purge_min_confidence': self.ai_purge_min_confidence,
            'ai_purge_max_ratio': self.ai_purge_max_ratio,
            'ai_purge_max_jobs': self.ai_purge_max_jobs,
            'llm_timeout_seconds': self.llm_timeout_seconds,
            'llm_max_retries': self.llm_max_retries,
            'llm_retry_base_delay': self.llm_retry_base_delay,
            'llm_retry_max_delay': self.llm_retry_max_delay,
            'llm_retry_jitter': self.llm_retry_jitter,
            'enable_description_parser': self.enable_description_parser,
            'desc_parser_model': self.desc_parser_model,
            'desc_parser_batch_size': self.desc_parser_batch_size,
            'desc_parser_max_batches': self.desc_parser_max_batches,
            'desc_parser_version': self.desc_parser_version,
            'desc_parser_min_chars': self.desc_parser_min_chars,
            'desc_parser_max_chars': self.desc_parser_max_chars,
            'desc_parser_dry_run': self.desc_parser_dry_run,
            'desc_parser_concurrency': self.desc_parser_concurrency,
            'scrape_descriptions_on_search': self.scrape_descriptions_on_search,
            'scrape_descriptions_limit': self.scrape_descriptions_limit,
            'enable_linkedin': self.enable_linkedin,
            'enable_indeed': self.enable_indeed,
            'linkedin_max_search_pages': self.linkedin_max_search_pages,
            'indeed_max_search_pages': self.indeed_max_search_pages,
            'dry_run': self.dry_run
        }
