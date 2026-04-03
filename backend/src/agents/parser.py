"""
Description Parser
- Incremental, cached parsing of job descriptions into structured JSON
- Uses an OpenAI-compatible API (via LLMConnection) for structured extraction
- Writes results into a separate SQLite table to avoid touching jobs.db schema
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
import hashlib
import json
import sqlite3
import sys
import re
from datetime import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator

from ..config.settings import Config
from ..utils.database import JobDatabase
from .job_scraper import JobScraper
# from ..utils.utilities import Utilities  # Temporarily commented out
from ..utils.llm_connection import LLMConnection


@dataclass
class ParsedDescription:
    job_id: str
    desc_hash: str
    version: int
    model: str
    payload_json: str  # raw JSON string from LLM
    created_at: str


class JobDescriptionStructure(BaseModel):
    """Pydantic model for structured job description parsing"""
    seniority: str = "unspecified"
    employment_type: str = "unspecified"
    remote: str = "unspecified"
    languages: List[str] = Field(default_factory=list)
    programming_languages: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    degree_field: str = "unspecified"
    degree_type: str = "unspecified"
    years_experience_min: Optional[int] = None
    location: List[str] = Field(default_factory=list)
    salary_eur_min: Optional[int] = None
    salary_eur_max: Optional[int] = None
    salary_eur_range: Optional[dict[str, Optional[int]]] = None
    extra_benefits: List[str] = Field(default_factory=list, alias="extra benefits")
    summary: str = "unspecified"

    model_config = {
        "populate_by_name": True,
        "extra": "ignore",
    }

    @model_validator(mode="before")
    @classmethod
    def _normalize_input_payload(cls, value: Any):
        if not isinstance(value, dict):
            return value

        data = dict(value)

        if "extra_benefits" not in data and "extra benefits" in data:
            data["extra_benefits"] = data.get("extra benefits")
        elif "extra benefits" not in data and "extra_benefits" in data:
            data["extra benefits"] = data.get("extra_benefits")

        data.setdefault("salary_eur_min", None)
        data.setdefault("salary_eur_max", None)
        data.setdefault("salary_eur_range", None)

        salary_range = data.get("salary_eur_range")
        if salary_range is None:
            range_min = data.get("salary_eur_min")
            range_max = data.get("salary_eur_max")
            if range_min is not None or range_max is not None:
                data["salary_eur_range"] = {
                    "min": range_min,
                    "max": range_max,
                }

        return data

    @field_validator(
        "languages",
        "programming_languages",
        "tools",
        "skills",
        "location",
        "extra_benefits",
        mode="before",
    )
    @classmethod
    def _coerce_string_list(cls, value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, tuple):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return []
            lowered = normalized.lower()
            if lowered in {"", "unspecified", "n/a", "na", "none", "null", "not specified"}:
                return []
            if normalized.startswith("[") and normalized.endswith("]"):
                try:
                    parsed = json.loads(normalized)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass
            split_values = []
            for chunk in re.split(r"\s*,\s*|\s*;\s*", normalized):
                chunk = chunk.strip().strip("[]{}()\"'")
                if chunk:
                    split_values.append(chunk)
            return split_values
        return [str(value).strip()] if str(value).strip() else []

    @staticmethod
    def _parse_salary_number(value):
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if not text or text in {"unspecified", "n/a", "na", "none", "null", "not specified", "negotiable"}:
                return None

            match = re.search(r"([\d]+(?:[.,]\d+)?)(\s*[km])?", text)
            if not match:
                return None

            number_text = match.group(1)
            suffix = (match.group(2) or "").strip()
            try:
                digits = re.sub(r"\D", "", number_text)
                if not digits:
                    return None
                number = float(digits)
            except (TypeError, ValueError):
                return None

            if suffix == "k":
                number *= 1000
            elif suffix == "m":
                number *= 1_000_000
            return int(round(number))
        return None

    @field_validator("salary_eur_min", "salary_eur_max", mode="before")
    @classmethod
    def _coerce_salary(cls, value):
        return cls._parse_salary_number(value)

    @field_validator("salary_eur_range", mode="before")
    @classmethod
    def _coerce_salary_range(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            parts = re.findall(r"(\d+(?:[.,]\d+)?\s*[km]?)", stripped, flags=re.IGNORECASE)
            if not parts:
                return None
            parsed = [cls._parse_salary_number(part) for part in parts]
            parsed = [p for p in parsed if p is not None]
            if not parsed:
                return None
            return {"min": parsed[0], "max": parsed[min(1, len(parsed) - 1)]}
        if isinstance(value, (list, tuple)):
            values = [cls._parse_salary_number(item) for item in value]
            values = [v for v in values if v is not None]
            if not values:
                return None
            return {"min": values[0], "max": values[min(1, len(values) - 1)]}
        if isinstance(value, dict):
            return {
                "min": cls._parse_salary_number(value.get("min")),
                "max": cls._parse_salary_number(value.get("max")),
            }
        return None

    @field_validator("years_experience_min", mode="before")
    @classmethod
    def _coerce_years_experience_min(cls, value):
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if not text or text in {"unspecified", "n/a", "na", "none", "null", "not specified"}:
                return None
            numbers = re.findall(r"\d+", text)
            if not numbers:
                return None
            try:
                return int(numbers[0])
            except (TypeError, ValueError):
                return None
        return None

    @classmethod
    def normalize_output_payload(cls, payload: dict) -> dict:
        if not isinstance(payload, dict):
            return {}
        data = dict(payload)
        salary_min = data.pop("salary_eur_min", None)
        salary_max = data.pop("salary_eur_max", None)

        salary_range = data.get("salary_eur_range")
        if not isinstance(salary_range, dict):
            salary_range = {"min": None, "max": None}

        range_min = salary_range.get("min")
        range_max = salary_range.get("max")
        if isinstance(range_min, str):
            range_min = cls._coerce_salary_like(range_min)
        if isinstance(range_max, str):
            range_max = cls._coerce_salary_like(range_max)
        if range_min is None and salary_min is not None:
            range_min = salary_min
        if range_max is None and salary_max is not None:
            range_max = salary_max

        data["salary_eur_range"] = {
            "min": cls._coerce_salary_like(range_min),
            "max": cls._coerce_salary_like(range_max),
        }
        return data

    @staticmethod
    def _coerce_salary_like(value: Any) -> Optional[int]:
        parsed = JobDescriptionStructure._parse_salary_number(value)
        return parsed

class DescriptionTools:
    """LLM-backed parser with caching based on (job_id, desc_hash, version).
    
    Uses structured generation with Pydantic models to ensure consistent JSON output
    from the LLM, improving reliability and reducing parsing errors.
    """

    def __init__(self, config: Config, db: Optional[JobDatabase] = None):
        self.config: Config = config
        self.db: JobDatabase = db or JobDatabase()
        self._ensure_table()
        self.desc_min_chars = max(0, int(getattr(self.config, 'desc_parser_min_chars', 80)))
        self.desc_max_chars = max(0, int(getattr(self.config, 'desc_parser_max_chars', 12000)))
        if self.desc_max_chars < self.desc_min_chars:
            self.desc_max_chars = self.desc_min_chars
        # Unified LLM connection, default to OpenAI model configured
        parser_model = getattr(self.config, 'desc_parser_model', None) or getattr(
            self.config, 'openai_model', 'gpt-5-mini'
        )
        parser_api_key = getattr(self.config, 'openai_api_key', '').strip() or None
        self.llm = LLMConnection(
            api_key=parser_api_key,
            model=parser_model,
            timeout_seconds=float(getattr(self.config, 'llm_timeout_seconds', 45)),
            max_retries=int(getattr(self.config, 'llm_max_retries', 3)),
            retry_base_delay=float(getattr(self.config, 'llm_retry_base_delay', 0.75)),
            retry_max_delay=float(getattr(self.config, 'llm_retry_max_delay', 8)),
            retry_jitter=float(getattr(self.config, 'llm_retry_jitter', 0.2)),
        )
        self.prompt: str = getattr(self.config, 'desc_parser_prompt', '')
        # Track how many characters the last progress update used so we can
        # properly clear the line on the next update.
        self._progress_line_length: int = 0

    def _ensure_table(self) -> None:
        """Create parsed_descriptions table if it doesn't exist (separate from jobs)."""
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS parsed_descriptions (
                    job_id TEXT NOT NULL,
                    desc_hash TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (job_id, desc_hash, version)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pd_job ON parsed_descriptions(job_id)")
            conn.commit()

    @staticmethod
    def _hash_description(text: str) -> str:
        return hashlib.sha256((text or '').encode('utf-8')).hexdigest()

    @staticmethod
    def _normalize_description_text(text: str) -> str:
        """Normalize noisy scraped descriptions for stable hashing and cleaner LLM inputs."""
        if not text:
            return ""

        normalized = str(text).replace("\r", "\n")
        normalized = re.sub(r"\u00a0", " ", normalized)  # non-breaking spaces
        normalized = re.sub(r"https?://\S+|www\.\S+", "", normalized)
        normalized = re.sub(r"(?im)^https?://\S+\s*$", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        normalized = normalized.replace("  ", " ")
        return normalized.strip()

    def _is_quality_description(self, text: str) -> bool:
        if not text:
            return False
        if len(text) < self.desc_min_chars:
            return False

        # Drop very short/empty lexical signals
        words = [w for w in re.split(r"\W+", text.lower()) if w]
        if len(words) < max(3, int(self.desc_min_chars / 6) if self.desc_min_chars else 3):
            return False

        # Remove extremely repetitive content such as "apply now" placeholders only
        meaningful = re.sub(r"(apply now|you are applying|click here|submit now|job id|location:)", "", text, flags=re.IGNORECASE)
        if len(meaningful.strip()) < max(3, int(self.desc_min_chars * 0.75)):
            return False

        return True

    def _prepare_description_for_llm(self, text: str) -> str:
        normalized = self._normalize_description_text(text)
        if not normalized:
            return ""
        if self.desc_max_chars and len(normalized) > self.desc_max_chars:
            return normalized[: self.desc_max_chars].strip()
        return normalized

    def _update_progress(self, batch_num: int, total_batches: int, job_current: int, job_total: int,
                          status: str = "") -> None:
        """Render a minimal single-line progress indicator."""
        try:
            # Clamp values to avoid negative progress or overflows
            if total_batches > 0:
                batch_position = max(0, min(batch_num, total_batches))
                batch_total_display = total_batches
            else:
                batch_position = max(batch_num, 0)
                batch_total_display = "?"

            if job_total > 0:
                job_position = max(0, min(job_current, job_total))
                pct = int((job_position / job_total) * 100)
                job_total_display = job_total
            else:
                job_position = max(job_current, 0)
                pct = 0
                job_total_display = "?"

            line = f"Batch {batch_position}/{batch_total_display} | Job {job_position}/{job_total_display} ({pct:3d}%)"

            if status:
                status_clean = status.strip().replace("\n", " ")
                max_status_length = 60
                if len(status_clean) > max_status_length:
                    status_clean = status_clean[:max_status_length - 3] + "..."
                line += f" - {status_clean}"

            if not sys.stdout.isatty():
                if job_total > 0:
                    step = max(1, int(job_total) // 10)
                    if job_position == 0 or job_position >= job_total or job_position % step == 0:
                        logger.info(line)
                else:
                    logger.info(line)
                return

            padding = max(self._progress_line_length - len(line), 0)
            sys.stdout.write("\r" + line + " " * padding)
            sys.stdout.flush()
            self._progress_line_length = max(self._progress_line_length, len(line))

            if job_total > 0 and job_position >= job_total:
                sys.stdout.write("\n")
                sys.stdout.flush()
                self._progress_line_length = 0

        except Exception:
            # Fallback silently if stdout is not available
            pass

    def _find_cached(self, job_id: str, desc_hash: str, version: int) -> Optional[ParsedDescription]:
        with sqlite3.connect(self.db.db_path) as conn:
            cur = conn.execute(
                "SELECT job_id, desc_hash, version, model, payload_json, created_at FROM parsed_descriptions WHERE job_id = ? AND desc_hash = ? AND version = ?",
                (job_id, desc_hash, version),
            )
            row = cur.fetchone()
            if not row:
                return None
            return ParsedDescription(*row)

    def _store(self, record: ParsedDescription) -> None:
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO parsed_descriptions(job_id, desc_hash, version, model, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (record.job_id, record.desc_hash, record.version, record.model, record.payload_json, record.created_at),
            )
            conn.commit()

    def _call_llm(
        self,
        description_text: str,
        job_id: str = "unknown",
        *,
        title: str = "Unknown Title",
        company: str = "Unknown Company",
    ) -> Optional[str]:
        """Call unified LLM using structured generation; return raw JSON text or None."""
        if not description_text.strip():
            logger.warning(f"Job {job_id}: Empty description, skipping LLM call")
            return None
        if not getattr(self.llm, 'api_key', ''):
            logger.error("OPENAI_API_KEY missing; cannot parse descriptions")
            return None

        prompt = (
            f"{self.prompt}\n\n"
            f"JOB_TITLE: {str(title).strip()}\n"
            f"COMPANY: {str(company).strip()}\n\n"
            f"DESCRIPTION:\n{description_text.strip()}\n\n"
            "Use ONLY the provided job title, company, and description text for extraction. "
            "Return valid JSON only. "
            "If fields are unclear, use 'unspecified' or null, not invented values."
        )
        
        # Add system message to clarify schema, especially for salary and benefits fields
        system_msg = ("Extract job information into the specified structure. "
                     "For salary, prefer salary_eur_min and salary_eur_max when available, both in EUR. "
                     "If only a range is available, set salary_eur_range with min/max and leave missing fields null. "
                     "If salary information is not available, use null. "
                     "For extra benefits, return a list of strings, never a single comma-separated string.")
        
        try:
            # Use structured generation with the Pydantic model
            result = self.llm.generate_structured(prompt, JobDescriptionStructure, system=system_msg)
            
            if result is None:
                logger.error(f"Job {job_id}: LLM returned None for structured generation")
                return None
            
            # Handle both parsed object and JSON string responses
            if isinstance(result, str):
                # If it's already a JSON string, validate it and normalize the payload shape.
                try:
                    parsed_json = json.loads(result)
                    if isinstance(parsed_json, dict):
                        try:
                            validated = JobDescriptionStructure.model_validate(parsed_json)
                            normalized = JobDescriptionStructure.normalize_output_payload(
                                validated.model_dump(by_alias=True)
                            )
                        except Exception:
                            normalized = JobDescriptionStructure.normalize_output_payload(parsed_json)
                        return json.dumps(normalized)
                    return result
                except json.JSONDecodeError as e:
                    logger.error(f"Job {job_id}: Invalid JSON returned from structured LLM call: {e}")
                    return None
            elif isinstance(result, JobDescriptionStructure):
                # If it's a parsed Pydantic object, convert to the expected JSON format
                # Convert flattened salary fields back to nested structure
                data = result.model_dump(by_alias=True)
                data = JobDescriptionStructure.normalize_output_payload(data)
                
                json_result = json.dumps(data)
                return json_result
            else:
                logger.error(f"Job {job_id}: Unexpected result type from LLM: {type(result)}")
                return None
                
        except Exception as e:
            logger.error(f"Job {job_id}: LLM structured request error: {e}")
            return None

    def parse_batch(self, jobs_df: pd.DataFrame, batch_num: int = 1, total_batches: int = 1) -> int:
        """Parse a batch of jobs with non-empty descriptions and no cache hit.
        Returns number of newly stored parses.
        """
        if jobs_df.empty:
            return 0
        version = int(getattr(self.config, 'desc_parser_version', 1))
        dry = bool(getattr(self.config, 'desc_parser_dry_run', False))
        # Allow parallel LLM calls; default to 10
        concurrency = max(1, int(getattr(self.config, 'desc_parser_concurrency', 10)))
        created_ts = datetime.utcnow().isoformat()
        
        new_count = 0
        total_items = int(len(jobs_df))
        completed_items = 0

        # Show initial progress indicator
        self._update_progress(batch_num, total_batches, 0, total_items, status="Starting batch")

        # Prepare jobs for processing: skip missing and cached first, then parallelize LLM calls
        jobs_to_process: List[Dict[str, Any]] = []
        for _, row in jobs_df.iterrows():
            original_desc = str(row.get('description') or '')
            desc = self._normalize_description_text(original_desc)
            job_id = str(row.get('job_id') or '')
            title = str(row.get('title', 'Unknown Title'))
            company = str(row.get('company', 'Unknown Company'))

            if not desc or not job_id:
                logger.warning(f"Job {job_id}: Skipping due to missing description or job_id")
                completed_items += 1
                self._update_progress(batch_num, total_batches, completed_items, total_items,
                                      status="Skipped — missing description or job_id")
                continue

            if not self._is_quality_description(desc):
                logger.debug(f"Job {job_id}: Skipping low-quality description")
                completed_items += 1
                self._update_progress(
                    batch_num,
                    total_batches,
                    completed_items,
                    total_items,
                    status="Skipped — low-quality description",
                )
                continue

            desc = self._prepare_description_for_llm(desc)
            if not desc:
                logger.warning(f"Job {job_id}: Skipping after normalization")
                completed_items += 1
                self._update_progress(batch_num, total_batches, completed_items, total_items, status="Skipped — cleaned to empty")
                continue

            h = self._hash_description(desc)
            cached = self._find_cached(job_id, h, version)
            if cached:
                logger.info(f"Job {job_id}: Found cached result, skipping LLM call")
                completed_items += 1
                self._update_progress(batch_num, total_batches, completed_items, total_items,
                                      status="Cached result found")
                continue

            jobs_to_process.append({
                'job_id': job_id,
                'title': title,
                'company': company,
                'description': desc,
                'desc_hash': h,
            })

        # If dry-run, bypass LLM and just store placeholders synchronously
        if dry:
            for item in jobs_to_process:
                rec = ParsedDescription(
                    job_id=item['job_id'],
                    desc_hash=item['desc_hash'],
                    version=version,
                    model=self.llm.model,
                    payload_json=json.dumps({"dry_run": True}),
                    created_at=created_ts,
                )
                try:
                    self._store(rec)
                    new_count += 1
                except Exception as e:
                    logger.error(f"Job {item['job_id']}: Failed to store parsed description (dry-run): {e}")
                finally:
                    completed_items += 1
                    self._update_progress(batch_num, total_batches, completed_items, total_items,
                                          status=f"Dry-run stored {item['title'][:30]}...")
        else:
            # Parallelize LLM calls, but serialize DB writes to avoid SQLite locks
            def worker(item: Dict[str, Any]) -> Dict[str, Any]:
                try:
                    payload = self._call_llm(
                        item['description'],
                        item['job_id'],
                        title=item['title'],
                        company=item['company'],
                    )
                    return {**item, 'payload': payload}
                except Exception as e:
                    logger.error(f"Job {item['job_id']}: Worker error: {e}")
                    return {**item, 'payload': None, 'error': str(e)}

            if jobs_to_process:
                status_msg = f"Dispatching {len(jobs_to_process)} LLM calls (concurrency={concurrency})"
                self._update_progress(batch_num, total_batches, completed_items, total_items, status=status_msg)

            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                future_map = {executor.submit(worker, item): item for item in jobs_to_process}
                for future in as_completed(future_map):
                    result = future.result()
                    job_id = result['job_id']
                    title = result['title']
                    payload = result.get('payload')

                    if not payload:
                        logger.error(f"Job {job_id}: LLM call failed, skipping storage")
                    else:
                        rec = ParsedDescription(
                            job_id=job_id,
                            desc_hash=result['desc_hash'],
                            version=version,
                            model=self.llm.model,
                            payload_json=payload,
                            created_at=created_ts,
                        )
                        try:
                            self._store(rec)
                            new_count += 1
                        except Exception as e:
                            logger.error(f"Job {job_id}: Failed to store parsed description: {e}")

                    completed_items += 1
                    running = len(jobs_to_process) - (completed_items - (total_items - len(jobs_to_process)))
                    status = f"{completed_items}/{total_items} done — {max(running,0)} running"
                    # Include a short title preview for user feedback
                    status = f"{status} | Last: {title[:30]}..."
                    self._update_progress(batch_num, total_batches, completed_items, total_items, status=status)

        # Final progress update
        self._update_progress(batch_num, total_batches, total_items, total_items, status="Batch complete")
        logger.info(f"Batch processing complete: {new_count} new descriptions parsed and stored")
        return new_count

    def parse_jobs_dataframe(
        self,
        jobs_df: pd.DataFrame,
        *,
        batch_size: Optional[int] = None,
        max_batches: Optional[int] = None,
        context_label: str = "jobs",
    ) -> int:
        """Parse descriptions for a pre-fetched DataFrame.

        This is used by scrape-time enrichment paths to avoid re-querying the DB
        before parsing.
        """
        if jobs_df.empty:
            return 0

        version = int(getattr(self.config, 'desc_parser_version', 1))
        if batch_size is None:
            batch_size = int(getattr(self.config, 'desc_parser_batch_size', 25))
        else:
            batch_size = int(batch_size)
        if max_batches is None:
            max_batches = int(getattr(self.config, 'desc_parser_max_batches', 4))
        else:
            max_batches = int(max_batches)

        if batch_size <= 0 or max_batches <= 0:
            logger.warning("Description parser settings invalid (batch_size/max_batches <= 0); skipping")
            return 0

        # Normalize and sanitize input
        working = jobs_df.copy()
        for column in ("job_id", "title", "company", "description"):
            if column not in working.columns:
                working[column] = ""
        working["job_id"] = working["job_id"].astype(str).str.strip()
        working["description"] = working["description"].astype(str).apply(self._prepare_description_for_llm)
        working["title"] = working["title"].astype(str).str.strip()
        working["company"] = working["company"].astype(str).str.strip()
        working = working[["job_id", "title", "company", "description"]]
        working = working[(working["job_id"] != "") & (working["description"] != "")]

        # Filter out low-quality descriptions before DB/caching work
        working["is_quality"] = working["description"].apply(self._is_quality_description)
        working = working[working["is_quality"]]
        working = working.drop(columns=["is_quality"])

        if working.empty:
            logger.info(f"Description parser ({context_label}) — no parseable descriptions in provided data")
            return 0

        working = working.copy()
        working["desc_hash"] = working["description"].astype(str).apply(self._hash_description)

        with sqlite3.connect(self.db.db_path) as conn:
            existing_df = pd.read_sql_query(
                "SELECT job_id, desc_hash FROM parsed_descriptions WHERE version = ?",
                conn,
                params=[version],
            )

        existing_set = set()
        if not existing_df.empty:
            existing_set = set(zip(existing_df["job_id"].astype(str), existing_df["desc_hash"].astype(str)))

        to_parse_mask = [
            (str(row["job_id"]), str(row["desc_hash"])) not in existing_set
            for _, row in working.iterrows()
        ]
        unparsed_df = working[to_parse_mask].copy()

        if unparsed_df.empty:
            logger.info(f"Description parser ({context_label}) — all provided jobs already parsed at version {version}")
            return 0

        max_to_process = min(len(unparsed_df), max_batches * batch_size)
        if max_to_process <= 0:
            return 0

        unparsed_df = unparsed_df.head(max_to_process)
        total_batches = (len(unparsed_df) + batch_size - 1) // batch_size
        if total_batches <= 0:
            return 0

        logger.info(
            f"Description parser ({context_label}): parsing {len(unparsed_df)} jobs "
            f"(max {max_to_process} from provided set, requested batches: {max_batches} x {batch_size})"
        )

        total_new = 0
        batches_processed = 0
        for batch_start in range(0, max_to_process, batch_size):
            batch_end = min(batch_start + batch_size, max_to_process)
            batch_df = unparsed_df.iloc[batch_start:batch_end]
            if batch_df.empty:
                continue
            batch_number = batches_processed + 1
            # remove desc_hash as parse_batch computes it and expects payload columns only
            processed = self.parse_batch(batch_df.drop(columns=["desc_hash"]), batch_number, total_batches)
            total_new += processed
            batches_processed += 1

        logger.info(
            f"Description parser ({context_label}) summary — parsed {total_new} / "
            f"{len(unparsed_df)} jobs (version={version}, batches={batches_processed}/{total_batches})"
        )
        if total_new > 0:
            try:
                self.db.recompute_fit_scores(
                    job_ids=unparsed_df["job_id"].astype(str).drop_duplicates().tolist()
                )
            except Exception as e:
                logger.warning(f"Could not recompute fit scores after parsing: {e}")
        return total_new

    def run_incremental(self, batch_size: int, max_batches: int) -> int:
        """Find jobs with descriptions and without cached parse.
        Processes at most max_batches batches of batch_size each.
        """
        with sqlite3.connect(self.db.db_path) as conn:
            all_jobs_df = pd.read_sql_query(
                """
                SELECT job_id, description, title, company
                FROM jobs
                WHERE description IS NOT NULL AND TRIM(description) <> ''
                ORDER BY datetime(COALESCE(scraped_at, created_at)) DESC
                """,
                conn,
            )

        return self.parse_jobs_dataframe(
            all_jobs_df,
            batch_size=batch_size,
            max_batches=max_batches,
            context_label="db-backfill",
        )


    def run_description_parser(self) -> bool:
        """Run the incremental description parser with caching, controlled via config knobs."""
        try:
            if not getattr(self.config, 'enable_description_parser', False):
                logger.info("ENABLE_DESCRIPTION_PARSER is disabled; skipping parsing")
                return True
            batch_size = int(getattr(self.config, 'desc_parser_batch_size', 25))
            max_batches = int(getattr(self.config, 'desc_parser_max_batches', 4))
            concurrency = int(getattr(self.config, 'desc_parser_concurrency', 10))
            logger.info(f"Running description parser: batch_size={batch_size}, max_batches={max_batches}, version={getattr(self.config, 'desc_parser_version', 1)}, concurrency={concurrency}")
            new_items = self.run_incremental(batch_size=batch_size, max_batches=max_batches)
            logger.info(f"Description parser stored {new_items} new parsed payloads")
            return True
        except Exception as e:
            logger.error(f"Description parser error: {e}")
            return False

    def backfill_missing_descriptions(self, scraper: JobScraper, batch_size: int = 50, max_batches: int = 10) -> bool:
        """Fetch and fill descriptions for jobs in DB missing descriptions.
        Processes up to (batch_size * max_batches) jobs with gentle pacing and rate-limit awareness.
        Jobs that fail to fetch descriptions are marked to avoid retrying.
        """
        try:
            total_updated = 0
            total_failed = 0
            batches_processed = 0
            consecutive_empty_batches = 0
            
            while batches_processed < max_batches:
                to_fill = scraper.db.get_jobs_missing_descriptions(limit=batch_size, exclude_failed=True)
                if to_fill.empty:
                    consecutive_empty_batches += 1
                    if consecutive_empty_batches >= 2:
                        break
                    time.sleep(1.0)  # Brief pause before checking again
                    continue
                else:
                    consecutive_empty_batches = 0

                logger.info(f"Backfill batch {batches_processed+1}: processing {len(to_fill)} jobs without descriptions")

                updates = []
                failed_job_ids = []
                total_in_batch = int(len(to_fill))
                completed_in_batch = 0
                scraper._update_progress(0, total_in_batch, prefix=f"Backfill batch {batches_processed+1}: ")

                for _, row in to_fill.iterrows():
                    url = str(row.get('url') or '')
                    source = str(row.get('source') or '')
                    job_id = str(row.get('job_id'))

                    if not url:
                        failed_job_ids.append(job_id)  # No URL to fetch from
                        completed_in_batch += 1
                        scraper._update_progress(completed_in_batch, total_in_batch, prefix=f"Backfill batch {batches_processed+1}: ")
                        continue

                    source = str(row.get('source') or '').strip()
                    desc = scraper._extract_job_description_from_url(source, url)
                    if desc and desc.strip():
                        updates.append({'job_id': job_id, 'description': desc})
                    else:
                        failed_job_ids.append(job_id)  # Failed to fetch or empty description

                    # gentle pacing between requests
                    time.sleep(max(0.5, scraper.config.request_delay))
                    completed_in_batch += 1
                    scraper._update_progress(completed_in_batch, total_in_batch, prefix=f"Backfill batch {batches_processed+1}: ")

                # Update successful descriptions
                if updates:
                    updated = scraper.db.update_job_descriptions(updates)
                    total_updated += updated

                # Mark failed attempts to avoid retrying
                if failed_job_ids:
                    marked = scraper.db.mark_description_fetch_failed(failed_job_ids)
                    total_failed += marked

                if not updates and not failed_job_ids:
                    logger.info("No descriptions could be fetched and no failures marked in this batch")

                batches_processed += 1

                # brief pause between batches with feedback
                if batches_processed < max_batches:
                    time.sleep(1.0)

            logger.success(f"Updated: {total_updated}, Failed/skipped: {total_failed}")
            return True
        except Exception as e:
            logger.error(f"Backfill error: {e}")
            return False
