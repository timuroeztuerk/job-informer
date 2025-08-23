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
    unwanted_companies: str
    
    # Scraping Configuration
    request_delay: float
    max_retries: int
    user_agent: str
    linkedin_max_pages: int
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
    # Description Parser Configuration
    enable_description_parser: bool
    desc_parser_model: str
    desc_parser_prompt: str
    desc_parser_batch_size: int
    desc_parser_max_batches: int
    desc_parser_version: int
    desc_parser_dry_run: bool

    # Feature Toggles
    enable_linkedin: bool
    dry_run: bool
    # Dry-run tuning
    dry_run_fast: bool
    dry_run_keywords_limit: int
    dry_run_locations_limit: int
    dry_run_pages: int
    dry_run_limit_per_source: int
    dry_run_request_delay: float
    dry_run_max_retries: int
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
            search_keywords=os.getenv('SEARCH_KEYWORDS', 'Data Science, Data Analysis, AI'),
            search_locations=os.getenv('SEARCH_LOCATIONS', 'Stuttgart, Berlin, Frankfurt, Köln, Ulm, Konstanz, Zürich, Düsseldorf, Freiburg, München, Augsburg, Nürnberg, Hannover'),
            unwanted_keywords=os.getenv('UNWANTED_KEYWORDS', 'adobe,abschluss,geo,volon,scrum,portfolio,financial,governance,labor,bestand,mergers,commodity,steuer,solution architect,finanzbuchhalter,projektmanager,gesundheit,laborant,logistikassistent,assistent,finanzbuchalter,reliability,Software Entwickler:in,Softwareingenieur,software engineer,solutions engineer,Gesundheitswissenschaftler,treasury,bioinformatiker,biologe,equity,retail,lehrkraft,cyber,creator,pwc,deloitte,auditor,phd,befristet,risk,compliance,public sector,microsoft,skillfinder,slurm,supplier,emat,praxis,operator,quality,medical,referent,last minute,assetmanagement,vermessungstechnikerin,powerbi,financial risk,vertriebssteuerung,ux designer,pricing,akademische/r,president,c++,devops,regulatory,assistent:in,projektkoordinator:in,cash,pay,sharepoint,teamlead,credit,sas,ontologien,photonics,convince,forensic,real estate,visual,opportunities,credit risk,life science,50%,produktmanager,produktbetreuer,kontakt-center,ce learning,kernel,think tank,aktuar,system,programmmanager,mathematiker,hr,gis,praktikant,geography,underwriter,controller,manager,ausbildung,hilfskraft,wiss.,Ingenieur,Research assistant,Pflichtpraktikum,Akademische:r,Studien-/Abschlussarbeit,bachelor,data collection,wissenschaflicher,chair,developer,threat,mapping,postdoctoral,power bi,hackers,masterthesis,masterarbeit,pharmaberater,abiturientenprogramm,biologist,customer,logistics,teilzeit,founders,D365,365,MSD365,scientist,founding,client,nebenberufliche*n,programme,executive,representative,energy,operations,talent,claims,application,entwicklungsingenieur,creative,sap,test,network,director,researcher,production,product,rwe,support,teil-,risikocontrolling,coordinator,crm,planner,risikomanagement,programm,abiturientenprogramm,security,produktionsplaner,supervisor,pharma,paralegal,Sicherheitstechniker,founder,head,working student,frontend,backend,techniker,manager,planer,nebenberuflich,full stack,lead,dual,duales,studium,controlling,berater,abitur,praktikum,marketing,verkäufer,internship,sales,freelance,werkstudent,intern,trainee,thesis,student,part-time,lecturer,tester'),
            unwanted_companies=os.getenv('UNWANTED_COMPANIES', 'universität,pwc,deloitte,nachhilfeunterricht'),
            
            # Scraping Configuration
            request_delay=float(os.getenv('REQUEST_DELAY', '3.0')),
            max_retries=int(os.getenv('MAX_RETRIES', '2')),
            # Default to a realistic desktop Chrome UA string
            user_agent=os.getenv('USER_AGENT', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'),
            linkedin_max_pages=int(os.getenv('LINKEDIN_MAX_PAGES', '3')),
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
            # Description Parser Configuration
            enable_description_parser=_get_bool('ENABLE_DESCRIPTION_PARSER', 'true'),
            desc_parser_model=os.getenv('DESC_PARSER_MODEL', os.getenv('GEMINI_MODEL', 'gemini-2.5-flash-lite')),
            desc_parser_prompt=os.getenv('DESC_PARSER_PROMPT', (
                'Extract the fields below from the job description and return STRICTLY VALID JSON.\n'
                'Rules: output JSON only (no markdown, no code fences, no prose). Use double quotes. No comments, no trailing commas, no ellipses.\n'
                'If unknown: use "unspecified" for enums, null for numbers, [] for arrays.\n'
                'Schema to return (exact keys/types):\n'
                '{\n'
                '  "seniority": "intern|junior|mid|senior|lead|principal|unspecified",\n'
                '  "employment_type": "full-time|part-time|contract|internship|unspecified",\n'
                '  "remote": "yes|no|hybrid|unspecified",\n'
                '  "languages": ["en", "de", "both"],\n'
                '  "programming_languages": ["python", "sql", "java", ...],\n'
                '  "tools": ["aws", "azure", "tensorflow", ...],\n'
                '  "skills": ["ml", "statistics", ...],\n'
                '  "degree_field": "computer science|data science|engineering|unspecified",\n'
                '  "degree_type": "bachelor|master|phd|unspecified",\n'
                '  "years_experience_min": 0,\n'
                '  "location": ["city in germany"],\n'
                '  "salary_eur_range": {"min": null, "max": null},\n'
                '  "extra benefits": "[flexible hours, deutschlandticket, gym, ...]",\n'
                '  "summary": "1-2 sentences, max 30 words"\n'
                '}'
            )),
            desc_parser_batch_size=int(os.getenv('DESC_PARSER_BATCH_SIZE', '25')),
            desc_parser_max_batches=int(os.getenv('DESC_PARSER_MAX_BATCHES', '10')),
            desc_parser_version=int(os.getenv('DESC_PARSER_VERSION', '1')),
            desc_parser_dry_run=_get_bool('DESC_PARSER_DRY_RUN', 'false'),

            # Feature Toggles
            enable_linkedin=_get_bool('ENABLE_LINKEDIN', 'true'),
            dry_run=_get_bool('DRY_RUN', 'false'),
            dry_run_fast=_get_bool('DRY_RUN_FAST', 'true'),
            dry_run_keywords_limit=int(os.getenv('DRY_RUN_KEYWORDS_LIMIT', '1')),
            dry_run_locations_limit=int(os.getenv('DRY_RUN_LOCATIONS_LIMIT', '1')),
            dry_run_pages=int(os.getenv('DRY_RUN_PAGES', '1')),
            dry_run_limit_per_source=int(os.getenv('DRY_RUN_LIMIT_PER_SOURCE', '10')),
            dry_run_request_delay=float(os.getenv('DRY_RUN_REQUEST_DELAY', '0.2')),
            dry_run_max_retries=int(os.getenv('DRY_RUN_MAX_RETRIES', '1')),
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
        gemini_required_modes = {'market-report', 'test', 'parse-descriptions', 'ai-purge', 'ai-purge-test'}

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
    
    def get_unwanted_companies_list(self) -> list:
        """Get unwanted companies as a list"""
        return [c.strip().lower() for c in self.unwanted_companies.split(',') if c.strip()]
    
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
            'unwanted_companies': self.unwanted_companies,
            'request_delay': self.request_delay,
            'max_retries': self.max_retries,
            'user_agent': self.user_agent,
            'linkedin_max_pages': self.linkedin_max_pages,
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
            'enable_description_parser': self.enable_description_parser,
            'desc_parser_model': self.desc_parser_model,
            'desc_parser_batch_size': self.desc_parser_batch_size,
            'desc_parser_max_batches': self.desc_parser_max_batches,
            'desc_parser_version': self.desc_parser_version,
            'desc_parser_dry_run': self.desc_parser_dry_run,
            'enable_linkedin': self.enable_linkedin,
            'dry_run': self.dry_run,
            'dry_run_fast': self.dry_run_fast,
            'dry_run_keywords_limit': self.dry_run_keywords_limit,
            'dry_run_locations_limit': self.dry_run_locations_limit,
            'dry_run_pages': self.dry_run_pages,
            'dry_run_limit_per_source': self.dry_run_limit_per_source,
            'dry_run_request_delay': self.dry_run_request_delay,
            'dry_run_max_retries': self.dry_run_max_retries,
            'auto_migrate_csv': self.auto_migrate_csv
        }
