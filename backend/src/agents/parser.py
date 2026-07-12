"""
Description Parser
- Incremental, cached parsing of job descriptions into structured JSON
- Uses the OpenAI Responses API for structured extraction
- Writes results into a separate SQLite table to avoid touching jobs.db schema
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union
import hashlib
import json
import sqlite3
import sys
import re
from datetime import UTC, datetime
import time

import pandas as pd
from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator

from ..config.settings import Config
from ..utils.database import JobDatabase
from ..utils.sqlite_connection import connect_sqlite
from ..utils.openai_responses_client import (
    LLMCallTelemetry,
    LLMUsage,
    OpenAIResponsesClient,
    StructuredLLMResult,
)
from .job_scraper import JobScraper


@dataclass
class ParsedDescription:
    job_id: str
    desc_hash: str
    version: int
    model: str
    payload_json: str  # raw JSON string from LLM
    created_at: str


class SalaryRange(BaseModel):
    min: Optional[int] = None
    max: Optional[int] = None

    model_config = {
        "extra": "forbid",
    }


class JobDescriptionStructure(BaseModel):
    """Pydantic model for structured job description parsing"""
    seniority: Literal["intern", "junior", "mid", "senior", "lead", "principal", "unspecified"] = "unspecified"
    employment_type: Literal["full-time", "part-time", "contract", "internship", "unspecified"] = "unspecified"
    remote: Literal["yes", "no", "hybrid", "unspecified"] = "unspecified"
    languages: List[str] = Field(default_factory=list)
    programming_languages: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    degree_field: str = "unspecified"
    degree_type: Literal["bachelor", "master", "phd", "unspecified"] = "unspecified"
    years_experience_min: Optional[int] = None
    location: List[str] = Field(default_factory=list)
    salary_eur_range: SalaryRange = Field(default_factory=SalaryRange)
    extra_benefits: List[str] = Field(default_factory=list, alias="extra benefits")
    summary: str = "unspecified"

    model_config = {
        "populate_by_name": True,
        "extra": "forbid",
    }

    @model_validator(mode="before")
    @classmethod
    def _normalize_input_payload(cls, value: Any):
        if not isinstance(value, dict):
            return value

        data = dict(value)

        if "extra benefits" in data:
            data.pop("extra_benefits", None)
        elif "extra_benefits" in data:
            data["extra benefits"] = data.pop("extra_benefits")

        salary_min = data.pop("salary_eur_min", None)
        salary_max = data.pop("salary_eur_max", None)
        salary_range = data.get("salary_eur_range")
        if salary_range is None:
            salary_range = {"min": salary_min, "max": salary_max}
        elif isinstance(salary_range, dict):
            salary_range = {
                "min": salary_range.get("min", salary_min),
                "max": salary_range.get("max", salary_max),
            }
        data["salary_eur_range"] = salary_range

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

    @field_validator("seniority", mode="before")
    @classmethod
    def _normalize_seniority(cls, value):
        clean = str(value or "").strip().lower()
        if clean in {"intern", "internship"}:
            return "intern"
        if clean in {"junior", "jr"}:
            return "junior"
        if clean in {"mid", "mid-level", "mid level", "regular"}:
            return "mid"
        if clean == "senior":
            return "senior"
        if clean == "lead":
            return "lead"
        if clean == "principal":
            return "principal"
        return "unspecified"

    @field_validator("employment_type", mode="before")
    @classmethod
    def _normalize_employment_type(cls, value):
        clean = str(value or "").strip().lower()
        if clean in {"full-time", "full time"}:
            return "full-time"
        if clean in {"part-time", "part time"}:
            return "part-time"
        if clean in {"contract", "contractor"}:
            return "contract"
        if clean in {"intern", "internship"}:
            return "internship"
        return "unspecified"

    @field_validator("remote", mode="before")
    @classmethod
    def _normalize_remote(cls, value):
        clean = str(value or "").strip().lower()
        if clean in {"yes", "remote", "fully remote"}:
            return "yes"
        if clean in {"no", "on-site", "onsite", "office"}:
            return "no"
        if clean == "hybrid":
            return "hybrid"
        return "unspecified"

    @field_validator("degree_type", mode="before")
    @classmethod
    def _normalize_degree_type(cls, value):
        clean = str(value or "").strip().lower()
        if clean in {"bachelor", "bachelors", "b.sc", "ba"}:
            return "bachelor"
        if clean in {"master", "masters", "m.sc", "msc", "ma"}:
            return "master"
        if clean in {"phd", "doctorate", "doctoral"}:
            return "phd"
        return "unspecified"

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

    @field_validator("salary_eur_range", mode="before")
    @classmethod
    def _coerce_salary_range(cls, value):
        if value is None:
            return {"min": None, "max": None}
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return {"min": None, "max": None}
            parts = re.findall(r"(\d+(?:[.,]\d+)?\s*[km]?)", stripped, flags=re.IGNORECASE)
            if not parts:
                return {"min": None, "max": None}
            parsed = [cls._parse_salary_number(part) for part in parts]
            parsed = [p for p in parsed if p is not None]
            if not parsed:
                return {"min": None, "max": None}
            return {"min": parsed[0], "max": parsed[min(1, len(parsed) - 1)]}
        if isinstance(value, (list, tuple)):
            values = [cls._parse_salary_number(item) for item in value]
            values = [v for v in values if v is not None]
            if not values:
                return {"min": None, "max": None}
            return {"min": values[0], "max": values[min(1, len(values) - 1)]}
        if isinstance(value, dict):
            return {
                "min": cls._parse_salary_number(value.get("min")),
                "max": cls._parse_salary_number(value.get("max")),
            }
        return {"min": None, "max": None}

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
        validated = cls.model_validate(payload)
        data = validated.model_dump(by_alias=True)
        if not isinstance(data.get("salary_eur_range"), dict):
            data["salary_eur_range"] = {"min": None, "max": None}
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

    def __init__(
        self,
        config: Config,
        db: Optional[JobDatabase] = None,
        llm_client: Optional[OpenAIResponsesClient] = None,
    ):
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
        self.llm = llm_client or OpenAIResponsesClient(
            api_key=parser_api_key,
            model=parser_model,
            timeout_seconds=float(getattr(self.config, 'llm_timeout_seconds', 45)),
            max_retries=int(getattr(self.config, 'llm_max_retries', 3)),
            retry_base_delay=float(getattr(self.config, 'llm_retry_base_delay', 0.75)),
            retry_max_delay=float(getattr(self.config, 'llm_retry_max_delay', 8)),
            retry_jitter=float(getattr(self.config, 'llm_retry_jitter', 0.2)),
        )
        self.prompt: str = getattr(self.config, 'desc_parser_prompt', '')
        self.max_output_tokens: int = 2048
        # Track how many characters the last progress update used so we can
        # properly clear the line on the next update.
        self._progress_line_length: int = 0

    def _ensure_table(self) -> None:
        """Create parsed_descriptions table if it doesn't exist (separate from jobs)."""
        with self.db._get_connection() as conn:
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
    def _is_dry_run_placeholder(payload_json: str) -> bool:
        """Identify legacy dry-run rows that are not real parser results."""
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            return False
        return isinstance(payload, dict) and payload.get("dry_run") is True

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
        with self.db._get_connection() as conn:
            cur = conn.execute(
                "SELECT job_id, desc_hash, version, model, payload_json, created_at FROM parsed_descriptions WHERE job_id = ? AND desc_hash = ? AND version = ?",
                (job_id, desc_hash, version),
            )
            row = cur.fetchone()
            if not row:
                return None
            cached = ParsedDescription(*row)
            if self._is_dry_run_placeholder(cached.payload_json):
                logger.info(f"Job {job_id}: Ignoring legacy dry-run cache placeholder")
                return None
            return cached

    def _store(self, record: ParsedDescription, *, conn: Optional[sqlite3.Connection] = None) -> None:
        owns_connection = conn is None
        active_conn = conn or connect_sqlite(self.db.db_path)
        try:
            existing = active_conn.execute(
                """
                SELECT payload_json
                FROM parsed_descriptions
                WHERE job_id = ? AND desc_hash = ? AND version = ?
                """,
                (record.job_id, record.desc_hash, record.version),
            ).fetchone()
            placeholder_replaced = False
            if existing and self._is_dry_run_placeholder(existing[0]):
                # Replace only the placeholder value we inspected. The extra
                # predicate prevents overwriting a real result written by a
                # concurrent parser between this read and update.
                cursor = active_conn.execute(
                    """
                    UPDATE parsed_descriptions
                    SET model = ?, payload_json = ?, created_at = ?
                    WHERE job_id = ? AND desc_hash = ? AND version = ?
                      AND payload_json = ?
                    """,
                    (
                        record.model,
                        record.payload_json,
                        record.created_at,
                        record.job_id,
                        record.desc_hash,
                        record.version,
                        existing[0],
                    ),
                )
                placeholder_replaced = cursor.rowcount > 0
            if not placeholder_replaced:
                # If a concurrent writer changed or removed the placeholder,
                # keep any real result it wrote or fill the now-empty slot.
                active_conn.execute(
                    """
                    INSERT OR IGNORE INTO parsed_descriptions(job_id, desc_hash, version, model, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (record.job_id, record.desc_hash, record.version, record.model, record.payload_json, record.created_at),
                )
            if owns_connection:
                active_conn.commit()
        finally:
            if owns_connection:
                active_conn.close()

    def _build_parser_instructions(self) -> str:
        prompt = str(self.prompt or "").strip()
        if prompt:
            return prompt
        return (
            "Extract job information from the provided title, company, and description. "
            "Use only the provided text. If a field is unclear, use 'unspecified', null, or an empty list."
        )

    @staticmethod
    def _build_parser_input(description_text: str, *, title: str, company: str) -> str:
        return (
            f"JOB_TITLE: {str(title).strip()}\n"
            f"COMPANY: {str(company).strip()}\n\n"
            f"DESCRIPTION:\n{description_text.strip()}\n"
        )

    @staticmethod
    def _serialize_parsed_payload(parsed: JobDescriptionStructure) -> str:
        payload = JobDescriptionStructure.normalize_output_payload(parsed.model_dump(by_alias=True))
        return json.dumps(payload)

    async def _call_llm_async(
        self,
        description_text: str,
        job_id: str = "unknown",
        *,
        title: str = "Unknown Title",
        company: str = "Unknown Company",
    ) -> StructuredLLMResult[JobDescriptionStructure]:
        """Call the shared Responses API client and return structured telemetry."""
        if not description_text.strip():
            logger.warning(f"Job {job_id}: Empty description, skipping LLM call")
            created_at = datetime.now(UTC).isoformat()
            return StructuredLLMResult(
                parsed=None,
                telemetry=LLMCallTelemetry(
                    model=self.llm.model,
                    status="failed",
                    started_at=created_at,
                    finished_at=created_at,
                    latency_ms=0.0,
                    error_type="EmptyDescription",
                    error_message="Empty description, skipping LLM call.",
                    usage=LLMUsage(),
                ),
            )

        return await self.llm.parse_structured_async(
            response_model=JobDescriptionStructure,
            input=self._build_parser_input(description_text, title=title, company=company),
            instructions=self._build_parser_instructions(),
            max_output_tokens=self.max_output_tokens,
        )

    def _call_llm(
        self,
        description_text: str,
        job_id: str = "unknown",
        *,
        title: str = "Unknown Title",
        company: str = "Unknown Company",
    ) -> Optional[str]:
        """Sync compatibility wrapper returning the canonical payload JSON string."""
        if not getattr(self.llm, 'api_key', ''):
            logger.error("OPENAI_API_KEY missing; cannot parse descriptions")
            return None

        try:
            result = self.llm.parse_structured(
                response_model=JobDescriptionStructure,
                input=self._build_parser_input(description_text, title=title, company=company),
                instructions=self._build_parser_instructions(),
                max_output_tokens=self.max_output_tokens,
            )
        except Exception as e:
            logger.error(f"Job {job_id}: LLM structured request error: {e}")
            return None

        if result.parsed is None:
            if result.telemetry.refusal_text:
                logger.warning(f"Job {job_id}: LLM refusal: {result.telemetry.refusal_text}")
            elif result.telemetry.error_message:
                logger.error(f"Job {job_id}: LLM structured request error: {result.telemetry.error_message}")
            return None

        return self._serialize_parsed_payload(result.parsed)

    async def _dispatch_parse_requests(
        self,
        jobs_to_process: List[Dict[str, Any]],
        *,
        batch_num: int,
        total_batches: int,
        total_items: int,
        completed_offset: int,
        concurrency: int,
    ) -> List[Dict[str, Any]]:
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def worker(item: Dict[str, Any]) -> Dict[str, Any]:
            async with semaphore:
                result = await self._call_llm_async(
                    item["description"],
                    item["job_id"],
                    title=item["title"],
                    company=item["company"],
                )
                return {**item, "result": result}

        tasks = [asyncio.create_task(worker(item)) for item in jobs_to_process]
        completed = completed_offset
        results: List[Dict[str, Any]] = []
        for future in asyncio.as_completed(tasks):
            result = await future
            results.append(result)
            completed += 1
            running = max(0, len(jobs_to_process) - len(results))
            telemetry = result["result"].telemetry
            status = f"{completed}/{total_items} done - {running} running | {telemetry.status}: {result['title'][:30]}..."
            self._update_progress(batch_num, total_batches, completed, total_items, status=status)
        return results

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
        created_ts = datetime.now(UTC).isoformat()
        
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

        # A dry run is a read-only preview of work that a real run would send.
        # In particular, it must not populate either the canonical cache or a
        # successful parse state, because that would suppress the real run.
        if dry:
            for item in jobs_to_process:
                logger.info(f"Job {item['job_id']}: Dry-run eligible for parsing")
                completed_items += 1
                self._update_progress(
                    batch_num,
                    total_batches,
                    completed_items,
                    total_items,
                    status=f"Dry-run eligible: {item['title'][:30]}...",
                )
        else:
            if jobs_to_process:
                status_msg = f"Dispatching {len(jobs_to_process)} LLM calls (concurrency={concurrency})"
                self._update_progress(batch_num, total_batches, completed_items, total_items, status=status_msg)

            results = asyncio.run(
                self._dispatch_parse_requests(
                    jobs_to_process,
                    batch_num=batch_num,
                    total_batches=total_batches,
                    total_items=total_items,
                    completed_offset=completed_items,
                    concurrency=concurrency,
                )
            )
            completed_items = total_items

            with self.db._get_connection() as conn:
                for result in results:
                    job_id = result["job_id"]
                    telemetry = result["result"].telemetry
                    parsed_payload = result["result"].parsed
                    metadata = {
                        "desc_hash": result["desc_hash"],
                        "version": version,
                        "title": result["title"],
                        "company": result["company"],
                    }
                    self.db.record_llm_attempt(
                        task_type="parser",
                        entity_id=job_id,
                        entity_hash=result["desc_hash"],
                        version=version,
                        model=telemetry.model,
                        status=telemetry.status,
                        started_at=telemetry.started_at,
                        finished_at=telemetry.finished_at,
                        latency_ms=telemetry.latency_ms,
                        response_id=telemetry.response_id,
                        retry_count=telemetry.retry_count,
                        exhausted_retries=telemetry.exhausted_retries,
                        input_tokens=telemetry.usage.input_tokens,
                        output_tokens=telemetry.usage.output_tokens,
                        total_tokens=telemetry.usage.total_tokens,
                        refusal_text=telemetry.refusal_text,
                        error_type=telemetry.error_type,
                        error_message=telemetry.error_message,
                        output_preview=telemetry.output_preview,
                        metadata=metadata,
                        conn=conn,
                    )

                    if telemetry.status == "success" and parsed_payload is not None:
                        rec = ParsedDescription(
                            job_id=job_id,
                            desc_hash=result["desc_hash"],
                            version=version,
                            model=telemetry.model,
                            payload_json=self._serialize_parsed_payload(parsed_payload),
                            created_at=created_ts,
                        )
                        try:
                            self._store(rec, conn=conn)
                            self.db.upsert_parse_job_state(
                                job_id=job_id,
                                desc_hash=result["desc_hash"],
                                version=version,
                                model=telemetry.model,
                                status="success",
                                last_attempt_at=telemetry.finished_at,
                                last_success_at=telemetry.finished_at,
                                last_response_id=telemetry.response_id,
                                output_preview=telemetry.output_preview,
                                conn=conn,
                            )
                            new_count += 1
                        except Exception as e:
                            logger.error(f"Job {job_id}: Failed to store parsed description: {e}")
                            self.db.upsert_parse_job_state(
                                job_id=job_id,
                                desc_hash=result["desc_hash"],
                                version=version,
                                model=telemetry.model,
                                status="failed",
                                last_attempt_at=telemetry.finished_at,
                                last_error_type="StorageError",
                                last_error_message=str(e),
                                last_response_id=telemetry.response_id,
                                output_preview=telemetry.output_preview,
                                conn=conn,
                            )
                    else:
                        self.db.upsert_parse_job_state(
                            job_id=job_id,
                            desc_hash=result["desc_hash"],
                            version=version,
                            model=telemetry.model,
                            status=telemetry.status,
                            last_attempt_at=telemetry.finished_at,
                            last_error_type=telemetry.error_type,
                            last_error_message=telemetry.error_message,
                            last_refusal_text=telemetry.refusal_text,
                            last_response_id=telemetry.response_id,
                            output_preview=telemetry.output_preview,
                            conn=conn,
                        )
                conn.commit()

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

        with self.db._get_connection() as conn:
            existing_df = pd.read_sql_query(
                "SELECT job_id, desc_hash, payload_json FROM parsed_descriptions WHERE version = ?",
                conn,
                params=[version],
            )

        existing_set = set()
        if not existing_df.empty:
            canonical_df = existing_df[
                ~existing_df["payload_json"].apply(self._is_dry_run_placeholder)
            ]
            existing_set = set(zip(canonical_df["job_id"].astype(str), canonical_df["desc_hash"].astype(str)))

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
        with self.db._get_connection() as conn:
            all_jobs_df = pd.read_sql_query(
                """
                SELECT job_id, description, title, company
                FROM jobs
                WHERE archived_at IS NULL
                AND description IS NOT NULL AND TRIM(description) <> ''
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

    def backfill_missing_descriptions(
        self,
        scraper: JobScraper,
        batch_size: Optional[int] = None,
        max_batches: Optional[int] = None,
    ) -> bool:
        """Fetch and fill descriptions for jobs in DB missing descriptions.

        ``max_batches=None`` drains the backlog until the first empty query.
        A finite value processes at most that many non-empty batches; values at
        or below zero are a successful no-op. Jobs that fail to fetch a
        description are marked to avoid retrying.
        """
        try:
            if batch_size is None:
                batch_size = int(getattr(self.config, 'scrape_descriptions_limit', 20) or 20)
            else:
                batch_size = int(batch_size)
            if batch_size <= 0:
                batch_size = 50

            if max_batches is not None:
                max_batches = int(max_batches)
                if max_batches <= 0:
                    logger.info("Description backfill batch limit is non-positive; nothing to process")
                    return True

            total_updated = 0
            total_failed = 0
            batches_processed = 0

            while max_batches is None or batches_processed < max_batches:
                to_fill = scraper.db.get_jobs_missing_descriptions(limit=batch_size, exclude_failed=True)
                if to_fill.empty:
                    break

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
                if max_batches is None or batches_processed < max_batches:
                    time.sleep(1.0)

            logger.success(f"Updated: {total_updated}, Failed/skipped: {total_failed}")
            return True
        except Exception as e:
            logger.error(f"Backfill error: {e}")
            return False
