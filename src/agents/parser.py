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
from datetime import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from loguru import logger
from pydantic import BaseModel, Field

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
    seniority: str
    employment_type: str
    remote: str
    languages: List[str]
    programming_languages: List[str]
    tools: List[str]
    skills: List[str]
    degree_field: str
    degree_type: str
    years_experience_min: Optional[int]
    location: List[str]
    salary_eur_min: Optional[int]
    salary_eur_max: Optional[int] 
    extra_benefits: List[str] = Field(alias="extra benefits")
    summary: str
    
    model_config = {"populate_by_name": True}

class DescriptionTools:
    """LLM-backed parser with caching based on (job_id, desc_hash, version).
    
    Uses structured generation with Pydantic models to ensure consistent JSON output
    from the LLM, improving reliability and reducing parsing errors.
    """

    def __init__(self, config: Config, db: Optional[JobDatabase] = None):
        self.config: Config = config
        self.db: JobDatabase = db or JobDatabase()
        self._ensure_table()
        # Unified LLM connection, default to OpenAI model configured
        parser_model = getattr(self.config, 'desc_parser_model', None) or getattr(
            self.config, 'openai_model', 'gpt-5-mini'
        )
        parser_api_key = getattr(self.config, 'openai_api_key', '').strip() or None
        self.llm = LLMConnection(api_key=parser_api_key, model=parser_model)
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
                line += f" — {status_clean}"

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

    def _call_llm(self, description_text: str, job_id: str = "unknown") -> Optional[str]:
        """Call unified LLM using structured generation; return raw JSON text or None."""
        if not description_text.strip():
            logger.warning(f"Job {job_id}: Empty description, skipping LLM call")
            return None
        if not getattr(self.llm, 'api_key', ''):
            logger.error("OPENAI_API_KEY missing; cannot parse descriptions")
            return None
        
        prompt = f"{self.prompt}\n\nDESCRIPTION:\n{description_text.strip()}\n\nRespond with JSON only."
        
        # Add system message to clarify schema, especially for salary fields
        system_msg = ("Extract job information into the specified structure. "
                     "For salary_eur_min/salary_eur_max: use separate fields for min and max salary in EUR. "
                     "Use null if salary information is not available.")
        
        try:
            # Use structured generation with the Pydantic model
            result = self.llm.generate_structured(prompt, JobDescriptionStructure, system=system_msg)
            
            if result is None:
                logger.error(f"Job {job_id}: LLM returned None for structured generation")
                return None
            
            # Handle both parsed object and JSON string responses
            if isinstance(result, str):
                # If it's already a JSON string, validate it
                try:
                    parsed_json = json.loads(result)
                    return result
                except json.JSONDecodeError as e:
                    logger.error(f"Job {job_id}: Invalid JSON returned from structured LLM call: {e}")
                    return None
            elif isinstance(result, JobDescriptionStructure):
                # If it's a parsed Pydantic object, convert to the expected JSON format
                # Convert flattened salary fields back to nested structure
                data = result.model_dump(by_alias=True)
                
                # Convert flat salary fields to nested structure
                if "salary_eur_min" in data or "salary_eur_max" in data:
                    data["salary_eur_range"] = {
                        "min": data.pop("salary_eur_min", None),
                        "max": data.pop("salary_eur_max", None)
                    }
                
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
            desc = str(row.get('description') or '').strip()
            job_id = str(row.get('job_id') or '')
            title = str(row.get('title', 'Unknown Title'))
            company = str(row.get('company', 'Unknown Company'))

            if not desc or not job_id:
                logger.warning(f"Job {job_id}: Skipping due to missing description or job_id")
                completed_items += 1
                self._update_progress(batch_num, total_batches, completed_items, total_items,
                                      status="Skipped — missing description or job_id")
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
                    payload = self._call_llm(item['description'], item['job_id'])
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

    def run_incremental(self, batch_size: int, max_batches: int) -> int:
        """Find jobs with descriptions and without cached parse.
        Processes at most max_batches batches of batch_size each.
        """
        total_new = 0
        version = int(getattr(self.config, 'desc_parser_version', 1))
        batches_processed = 0
        
        with sqlite3.connect(self.db.db_path) as conn:
            # Get all jobs with descriptions that haven't been parsed at this version
            # We'll compute the hash in Python since it's more reliable
            all_jobs_df = pd.read_sql_query(
                """
                SELECT job_id, description, title, company
                FROM jobs
                WHERE description IS NOT NULL AND TRIM(description) <> ''
                ORDER BY datetime(COALESCE(scraped_at, created_at)) DESC
                """,
                conn
            )
            
            if all_jobs_df.empty:
                logger.info("Description parser summary — no jobs with descriptions found")
                return 0
            
            # Add description hash column
            all_jobs_df['desc_hash'] = all_jobs_df['description'].astype(str).apply(self._hash_description)
            
            # Get existing parsed records for this version
            existing_df = pd.read_sql_query(
                "SELECT job_id, desc_hash FROM parsed_descriptions WHERE version = ?",
                conn,
                params=[version]
            )
            
            # Create a set of (job_id, desc_hash) tuples for O(1) lookup
            existing_set = set()
            if not existing_df.empty:
                existing_set = set(zip(existing_df['job_id'].astype(str), existing_df['desc_hash'].astype(str)))
            
            # Filter out already parsed jobs
            mask = [
                (str(row['job_id']), str(row['desc_hash'])) not in existing_set 
                for _, row in all_jobs_df.iterrows()
            ]
            unparsed_df = all_jobs_df[mask].copy()
            
            if unparsed_df.empty:
                logger.info("Description parser summary — all descriptions already parsed at this version")
                return 0
            
            total_unparsed = len(unparsed_df)
            max_to_process = min(total_unparsed, max_batches * batch_size)
            total_planned_batches = min(max_batches, (max_to_process + batch_size - 1) // batch_size)
            
            logger.info(f"Description parser: found {total_unparsed} unparsed descriptions, will process up to {max_to_process} (max {total_planned_batches} batches of {batch_size})")
            
            # Process in batches, respecting max_batches limit
            for batch_start in range(0, max_to_process, batch_size):
                if batches_processed >= max_batches:
                    logger.info(f"Reached maximum batch limit ({max_batches}), stopping")
                    break
                    
                batch_end = min(batch_start + batch_size, max_to_process)
                batch_df = unparsed_df.iloc[batch_start:batch_end].copy()
                
                # Remove desc_hash column before processing (parse_batch doesn't expect it)
                batch_for_processing = batch_df.drop(columns=['desc_hash'])
                
                # Process this batch with batch number information
                batch_number = batches_processed + 1
                processed = self.parse_batch(batch_for_processing, batch_number, total_planned_batches)
                total_new += processed
                batches_processed += 1
                
                logger.info(f"Description parser: processed batch {batches_processed}/{total_planned_batches}, parsed {processed}/{len(batch_df)} items")

        if total_new > 0:
            logger.info(
                f"Description parser summary — total_jobs:{len(all_jobs_df)} already_parsed:{len(all_jobs_df) - len(unparsed_df)} stored_new:{total_new} version:{version} batches_processed:{batches_processed}"
            )
        else:
            logger.info("Description parser summary — no new descriptions parsed")
        return total_new

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

                    desc = scraper._extract_linkedin_description(url)
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
