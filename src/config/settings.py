"""
Configuration Module
Handles application settings and environment variables
"""

import os
from dotenv import load_dotenv
from typing import Optional
from dataclasses import dataclass
import re


VALID_TIME_RANGES = {"day", "week", "month"}
DEFAULT_TIME_RANGE = "day"


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
    
    # AI Analysis Configuration
    openai_api_key: str
    openai_model: str
    
    # Description Parser Configuration
    enable_description_parser: bool
    desc_parser_model: str
    desc_parser_prompt: str
    desc_parser_batch_size: int
    desc_parser_max_batches: int
    desc_parser_version: int
    desc_parser_dry_run: bool
    desc_parser_concurrency: int

    # Feature Toggles
    enable_linkedin: bool
    dry_run: bool
    auto_purge_before_scraping: bool
    
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

        def _get_time_range(name: str, default: str = DEFAULT_TIME_RANGE) -> str:
            value = os.getenv(name, default)
            value = (value or default).strip().lower()
            if value not in VALID_TIME_RANGES:
                return DEFAULT_TIME_RANGE
            return value

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
            unwanted_keywords=os.getenv('UNWANTED_KEYWORDS', 'professor,traineeship,mitarbeiter,e-commerce,manager,adobe,abschluss,geo,volon,scrum,portfolio,financial,governance,labor,bestand,mergers,commodity,steuer,solution architect,finanzbuchhalter,projektmanager,gesundheit,laborant,logistikassistent,assistent,finanzbuchalter,reliability,Software Entwickler:in,Softwareingenieur,software engineer,solutions engineer,Gesundheitswissenschaftler,treasury,bioinformatiker,biologe,equity,retail,lehrkraft,cyber,creator,pwc,deloitte,auditor,phd,befristet,risk,compliance,public sector,microsoft,skillfinder,slurm,supplier,emat,praxis,operator,quality,medical,referent,last minute,assetmanagement,vermessungstechnikerin,powerbi,financial risk,vertriebssteuerung,ux designer,pricing,akademische/r,president,c++,devops,regulatory,assistent:in,projektkoordinator:in,cash,pay,sharepoint,teamlead,credit,sas,ontologien,photonics,convince,forensic,real estate,visual,opportunities,credit risk,life science,50%,produktmanager,produktbetreuer,kontakt-center,ce learning,kernel,think tank,aktuar,system,programmmanager,mathematiker,hr,gis,praktikant,geography,underwriter,controller,ausbildung,hilfskraft,wiss.,Ingenieur,Research assistant,Pflichtpraktikum,Akademische:r,Studien-/Abschlussarbeit,bachelor,data collection,wissenschaflicher,chair,threat,mapping,postdoctoral,power bi,hackers,masterthesis,masterarbeit,pharmaberater,abiturientenprogramm,biologist,customer,logistics,teilzeit,founders,D365,365,MSD365,founding,client,nebenberufliche*n,programme,executive,representative,energy,operations,talent,claims,application,entwicklungsingenieur,creative,sap,test,network,director,researcher,production,product,rwe,support,teil-,risikocontrolling,coordinator,crm,planner,risikomanagement,programm,abiturientenprogramm,security,produktionsplaner,supervisor,pharma,paralegal,Sicherheitstechniker,founder,head,working student,frontend developer,backend developer,techniker,planer,nebenberuflich,full stack developer,lead developer,dual,duales,studium,controlling,berater,abitur,praktikum,marketing manager,project manager,sales manager,verkäufer,internship,sales,freelance,werkstudent,intern,trainee,thesis,student,part-time,lecturer,tester'),
            unwanted_companies=os.getenv('UNWANTED_COMPANIES', 'ey,mycareernow GmbH,universität,pwc,deloitte,nachhilfeunterricht'),
            
            # Scraping Configuration
            request_delay=float(os.getenv('REQUEST_DELAY', '2.0')),
            max_retries=int(os.getenv('MAX_RETRIES', '2')),
            # Default to a realistic desktop Chrome UA string
            user_agent=os.getenv('USER_AGENT', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'),
            max_total_jobs=int(os.getenv('MAX_TOTAL_JOBS', '0')),
            min_new_jobs_to_continue=int(os.getenv('MIN_NEW_JOBS_TO_CONTINUE', '1')),
            quiet_progress=_get_bool('QUIET_PROGRESS', 'true'),
            
            # Logging Configuration
            log_level=os.getenv('LOG_LEVEL', 'INFO'),
            log_file=os.getenv('LOG_FILE', 'logs/job_informer.log'),
            
            # AI Analysis Configuration
            openai_api_key=os.getenv('OPENAI_API_KEY', ''),
            openai_model=os.getenv('OPENAI_MODEL', 'gpt-5-mini'),
            
            # Description Parser Configuration
            enable_description_parser=_get_bool('ENABLE_DESCRIPTION_PARSER', 'true'),
            desc_parser_model=os.getenv('DESC_PARSER_MODEL', os.getenv('OPENAI_MODEL', 'gpt-5-mini')),
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
            desc_parser_concurrency=int(os.getenv('DESC_PARSER_CONCURRENCY', '25')),

            # Feature Toggles
            enable_linkedin=_get_bool('ENABLE_LINKEDIN', 'true'),
            dry_run=_get_bool('DRY_RUN', 'false'),
            auto_purge_before_scraping=_get_bool('AUTO_PURGE_BEFORE_SCRAPING', 'true')
        )
    
    def validate_for_mode(self, mode: str) -> None:
        """Validate configuration values depending on execution mode"""
        errors = []

        # Modes that require email configuration
        email_required_modes = {
            'run-once', 'test', 'summary', 'inform'
        }
        # Modes that require LLM access
        ai_required_modes = {'test', 'parse-descriptions', 'ai-purge'}

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
            'enable_description_parser': self.enable_description_parser,
            'desc_parser_model': self.desc_parser_model,
            'desc_parser_batch_size': self.desc_parser_batch_size,
            'desc_parser_max_batches': self.desc_parser_max_batches,
            'desc_parser_version': self.desc_parser_version,
            'desc_parser_dry_run': self.desc_parser_dry_run,
            'desc_parser_concurrency': self.desc_parser_concurrency,
            'enable_linkedin': self.enable_linkedin,
            'dry_run': self.dry_run
        }
