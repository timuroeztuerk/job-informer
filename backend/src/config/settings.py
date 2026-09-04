"""Configuration for the local LinkedIn collector."""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values, load_dotenv


VALID_TIME_RANGES = {"day", "week", "month"}
VALID_RELEVANCE_MODES = {"shadow", "enforce"}
DEFAULT_TIME_RANGE = "day"
BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LOG_FILE = str(BASE_DIR / "logs" / "job_informer.log")
DEFAULT_JOBS_DB_PATH = str(BASE_DIR / "data" / "jobs.db")
DEFAULT_UNWANTED_KEYWORDS = (
    "ai engineer,artificial intelligence engineer,generative ai engineer,genai engineer"
)


@dataclass
class Config:
    """Small, explicit configuration surface for manual collection."""

    search_keywords: str
    search_locations: str
    search_time_range: str
    unwanted_keywords: str
    unwanted_companies: str
    request_delay: float
    max_retries: int
    user_agent: str
    max_total_jobs: int
    min_new_jobs_to_continue: int
    quiet_progress: bool
    linkedin_max_search_pages: int
    relevance_mode: str
    archive_unmatched_jobs: bool
    log_level: str
    log_file: str
    jobs_db_path: str

    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> "Config":
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
                load_dotenv()

        raw_db_path = (
            explicit_jobs_db_path
            if explicit_jobs_db_path is not None
            else os.getenv("JOBS_DB_PATH", "").strip()
        )
        if raw_db_path:
            candidate = Path(raw_db_path).expanduser()
            if not candidate.is_absolute():
                candidate = (loaded_env_path.parent if loaded_env_path else BASE_DIR.parent) / candidate
            jobs_db_path = str(candidate.resolve())
        else:
            jobs_db_path = str(Path(DEFAULT_JOBS_DB_PATH).resolve())
        os.environ["JOBS_DB_PATH"] = jobs_db_path

        def get_bool(name: str, default: str = "false") -> bool:
            return bool(re.match(r"^(1|true|yes|y|on)$", os.getenv(name, default).strip(), re.I))

        def get_int(name: str, default: str) -> int:
            try:
                return int(os.getenv(name, default))
            except (TypeError, ValueError):
                return int(default)

        time_range = os.getenv("SEARCH_TIME_RANGE", DEFAULT_TIME_RANGE).strip().lower()
        if time_range not in VALID_TIME_RANGES:
            time_range = DEFAULT_TIME_RANGE

        return cls(
            search_keywords=os.getenv("SEARCH_KEYWORDS", "Data Scientist, Data Analyst"),
            search_locations=os.getenv(
                "SEARCH_LOCATIONS",
                "Stuttgart, Berlin, Frankfurt, Köln, Ulm, Konstanz, Zürich, Düsseldorf, Freiburg, München, Augsburg, Nürnberg, Hannover",
            ),
            search_time_range=time_range,
            unwanted_keywords=os.getenv("UNWANTED_KEYWORDS", DEFAULT_UNWANTED_KEYWORDS),
            unwanted_companies=os.getenv("UNWANTED_COMPANIES", ""),
            request_delay=float(os.getenv("REQUEST_DELAY", "2.0")),
            max_retries=get_int("MAX_RETRIES", "2"),
            user_agent=os.getenv(
                "USER_AGENT",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            ),
            max_total_jobs=get_int("MAX_TOTAL_JOBS", "0"),
            min_new_jobs_to_continue=get_int("MIN_NEW_JOBS_TO_CONTINUE", "1"),
            quiet_progress=get_bool("QUIET_PROGRESS", "true"),
            linkedin_max_search_pages=get_int("LINKEDIN_MAX_SEARCH_PAGES", "4"),
            relevance_mode=(
                os.getenv("RELEVANCE_MODE", "enforce").strip().lower()
                if os.getenv("RELEVANCE_MODE", "enforce").strip().lower() in VALID_RELEVANCE_MODES
                else "enforce"
            ),
            archive_unmatched_jobs=get_bool("ARCHIVE_UNMATCHED_JOBS", "false"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_file=os.getenv("LOG_FILE", DEFAULT_LOG_FILE),
            jobs_db_path=jobs_db_path,
        )

    def validate_for_mode(self, mode: str) -> None:
        if mode != "run-once":
            raise ValueError(f"Unsupported mode: {mode}")
        errors: list[str] = []
        if not self.search_keywords.strip():
            errors.append("SEARCH_KEYWORDS is required")
        if not self.search_locations.strip():
            errors.append("SEARCH_LOCATIONS is required")
        if self.search_time_range not in VALID_TIME_RANGES:
            errors.append("SEARCH_TIME_RANGE must be one of: day, week, month")
        if self.relevance_mode not in VALID_RELEVANCE_MODES:
            errors.append("RELEVANCE_MODE must be one of: shadow, enforce")
        if errors:
            raise ValueError(f"Configuration validation failed: {', '.join(errors)}")

    def validate(self) -> None:
        self.validate_for_mode("run-once")

    def get_keywords_list(self) -> list[str]:
        return [value.strip() for value in self.search_keywords.split(",") if value.strip()]

    def get_locations_list(self) -> list[str]:
        return [value.strip() for value in self.search_locations.split(",") if value.strip()]

    @staticmethod
    def _normalized_list(value: str) -> list[str]:
        return [
            unicodedata.normalize("NFD", item.strip().lower()).encode("ascii", "ignore").decode("ascii")
            for item in value.split(",")
            if item.strip()
        ]

    def get_unwanted_keywords_list(self) -> list[str]:
        return self._normalized_list(self.unwanted_keywords)

    def get_unwanted_companies_list(self) -> list[str]:
        return self._normalized_list(self.unwanted_companies)

    def to_dict(self) -> dict[str, object]:
        return {
            "search_keywords": self.search_keywords,
            "search_locations": self.search_locations,
            "search_time_range": self.search_time_range,
            "unwanted_keywords": self.unwanted_keywords,
            "unwanted_companies": self.unwanted_companies,
            "request_delay": self.request_delay,
            "max_retries": self.max_retries,
            "user_agent": self.user_agent,
            "max_total_jobs": self.max_total_jobs,
            "min_new_jobs_to_continue": self.min_new_jobs_to_continue,
            "quiet_progress": self.quiet_progress,
            "linkedin_max_search_pages": self.linkedin_max_search_pages,
            "relevance_mode": self.relevance_mode,
            "archive_unmatched_jobs": self.archive_unmatched_jobs,
            "log_level": self.log_level,
            "log_file": self.log_file,
            "jobs_db_path": self.jobs_db_path,
        }
