from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import json
from datetime import UTC, datetime
from loguru import logger
from pydantic import BaseModel, Field

from ..config.settings import Config
from ..utils.database import JobDatabase
from ..utils.openai_responses_client import OpenAIResponsesClient
from .parser import DescriptionTools

@dataclass
class JobEntry:
    """Job entry for AI analysis"""
    id: str
    title: str
    company: str


class AIPurgeCandidate(BaseModel):
    """Single structured AI purge decision."""
    id: str
    reason: str = Field(default="No reason provided", max_length=160)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    purge: bool = Field(default=True)


class AIPurger_JSON_CLASS(BaseModel):
    """Structured response model for AI purger."""
    purge_candidates: List[AIPurgeCandidate] = Field(default_factory=list)
    job_ids_to_purge: List[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=240)

class AIPurger:
    """
    AI Job Purger class
    Removes unwanted job postings using LLMs with caching.
    
    Processing Order: Always starts with the most recently added jobs to the database
    to ensure latest entries are prioritized for purging analysis.
    
    Caching: Jobs are marked as 'analyzed' (analyzed=1) after processing to avoid 
    re-analyzing them on subsequent runs. Only jobs with analyzed=0 or NULL are processed.
    
    By default, processes only the first batch (10 jobs) to avoid overwhelming the system.
    Set process_all=True in constructor to process all jobs in the database.
    """

    def __init__(
        self,
        config: Config,
        test_mode: bool = False,
        process_all: bool = True,
        db: Optional[JobDatabase] = None,
        llm_client: Optional[OpenAIResponsesClient] = None,
    ):
        self.config = config
        self.test_mode = test_mode
        self.process_all = process_all  # If True, processes all jobs; if False, processes only default batch size
        self.db = db or JobDatabase()
        self.parser = DescriptionTools(config=self.config, db=self.db)
        purge_model = getattr(self.config, 'openai_model', 'gpt-5-mini')
        purge_api_key = getattr(self.config, 'openai_api_key', '').strip() or None
        self.llm = llm_client or OpenAIResponsesClient(
            api_key=purge_api_key,
            model=purge_model,
            timeout_seconds=float(getattr(self.config, 'llm_timeout_seconds', 45)),
            max_retries=int(getattr(self.config, 'llm_max_retries', 3)),
            retry_base_delay=float(getattr(self.config, 'llm_retry_base_delay', 0.75)),
            retry_max_delay=float(getattr(self.config, 'llm_retry_max_delay', 8)),
            retry_jitter=float(getattr(self.config, 'llm_retry_jitter', 0.2)),
        )
        self.batch_size = 25
        self.min_confidence = max(0.0, min(1.0, float(getattr(self.config, 'ai_purge_min_confidence', 0.75))))
        self.max_ratio = max(0.0, min(1.0, float(getattr(self.config, 'ai_purge_max_ratio', 0.35))))
        self.max_jobs = max(0, int(getattr(self.config, 'ai_purge_max_jobs', 0)))
        self.purge_prompt = """
        You are an AI job filter. Analyze the provided job listings and identify jobs that should be purged.
        
        IMPORTANT: Be conservative and only purge jobs that are clearly irrelevant or low-quality.
        Looking at the job titles and companies, you should purge jobs that are:
        - Clearly IRRELEVANT to data science, data analysis, machine learning, AI, analytics engineering, data/platform engineering, or adjacent technical roles (e.g. sales, retail, manual labor, nursing, accounting, field service).
        - Study-track or internship-style roles such as internships, working-student jobs, thesis roles, apprenticeships, doctoral student roles, and similar student positions.
        - Obviously spam, duplicate, or very low-quality postings.
        - Keep adjacent technical roles if they are plausibly relevant to data/AI work. Do NOT purge a role just because it is software engineering, platform, backend, full stack, DevOps, or MLOps-adjacent.
        - AI consultant jobs can stay, if they are not duplicate.
        
        Each job listing will be labeled with a short numeric ID (e.g., "1", "2").
        Return JSON only, with this shape:
        {
          "purge_candidates": [
            {
              "id": "1",
              "reason": "Clear IRRELEVANT non-technical role",
              "confidence": 0.93,
              "purge": true
            }
          ],
          "job_ids_to_purge": ["1", "2"],
          "notes": "Optional short note"
        }
        Use the same ID format, and include both a reason and confidence for each candidate.
        Return candidates only for jobs that should be purged. Keep each reason to 12 words or fewer
        and keep notes to one short sentence.
        """
        self.json_structure = AIPurger_JSON_CLASS

    def _extract_id(self, value: Any) -> str:
        """Extract a stable string id from dict/object/string values."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float)):
            return str(int(value))
        if isinstance(value, dict):
            return str(value.get("id", "")).strip()
        return str(value).strip()

    def _coerce_confidence(self, value: Any, default: float) -> float:
        """Normalize confidence values to [0, 1]."""
        if value is None:
            return default
        try:
            conf = float(value)
        except (TypeError, ValueError):
            return default
        if conf < 0.0:
            return 0.0
        if conf > 1.0:
            return 1.0
        return conf

    def _normalize_candidate_payload(self, analysis: Any, reference_map: Dict[str, JobEntry],
                                    batch_job_ids: set[str]) -> List[Dict[str, Any]]:
        """
        Convert model output into a normalized internal candidate list.
        """
        decisions: List[Dict[str, Any]] = []
        raw_candidates: List[Any] = []

        if isinstance(analysis, dict):
            raw_candidates.extend(analysis.get("purge_candidates", []) or [])
            if not raw_candidates:
                legacy_ids = analysis.get("job_ids_to_purge") or []
                raw_candidates.extend([{"id": c} for c in legacy_ids])
        else:
            if hasattr(analysis, "purge_candidates"):
                raw_candidates.extend(getattr(analysis, "purge_candidates") or [])
            if hasattr(analysis, "job_ids_to_purge"):
                raw_candidates.extend([{"id": c} for c in (getattr(analysis, "job_ids_to_purge") or [])])

        for raw in raw_candidates:
            candidate_id = self._extract_id(getattr(raw, "id", raw.get("id") if isinstance(raw, dict) else raw))
            if not candidate_id:
                continue

            if hasattr(raw, "reason"):
                reason = str(getattr(raw, "reason", "")).strip() or "No reason provided"
            elif isinstance(raw, dict):
                reason = str(raw.get("reason", "No reason provided")).strip() or "No reason provided"
            else:
                reason = "No reason provided"

            if hasattr(raw, "confidence"):
                raw_confidence = getattr(raw, "confidence")
            elif isinstance(raw, dict):
                raw_confidence = raw.get("confidence")
            else:
                raw_confidence = None
            confidence = self._coerce_confidence(raw_confidence, self.min_confidence)

            if hasattr(raw, "purge"):
                raw_purge = getattr(raw, "purge")
            elif isinstance(raw, dict):
                raw_purge = raw.get("purge")
            else:
                raw_purge = None
            if isinstance(raw_purge, str):
                purge = raw_purge.strip().lower() in {"true", "1", "yes", "y", "on"}
            elif isinstance(raw_purge, (int, float)):
                purge = bool(raw_purge)
            elif raw_purge is None:
                purge = True
            else:
                purge = bool(raw_purge)

            if not purge:
                continue

            if candidate_id in reference_map:
                actual_id = reference_map[candidate_id].id
            elif candidate_id in batch_job_ids:
                actual_id = candidate_id
            else:
                continue

            # Default legacy fallback: if confidence was not supplied, keep at threshold.
            if raw_confidence is None:
                confidence = max(confidence, self.min_confidence)

            if confidence < self.min_confidence:
                continue

            decisions.append({
                "job_id": actual_id,
                "id": candidate_id,
                "reason": reason,
                "confidence": confidence,
                "purge": purge,
                "decision_source": "ai",
                "decision_action": "archive",
                "filter_name": "ai_relevance_review",
                "matched_value": None,
                "details": {
                    "reference_id": candidate_id,
                    "llm_reason": reason,
                },
            })

        seen: set[str] = set()
        unique: List[Dict[str, Any]] = []
        for item in decisions:
            job_id = item["job_id"]
            if job_id in seen:
                continue
            seen.add(job_id)
            unique.append(item)

        return unique

    def prepare(self) -> Optional[Dict[str, Any]]:
        """
        Analyze jobs and return analysis results
        Returns None on error, dict with results on success
        """
        if not getattr(self.llm, 'api_key', '') or not self.purge_prompt.strip():
            return None
        
        # Show analysis status
        status = self.get_analysis_status()
        logger.info(f"Analysis status: {status['unanalyzed']} unanalyzed, {status['analyzed']} analyzed, {status['total']} total jobs")
        
        # Get jobs from database, starting with the latest added jobs that haven't been analyzed yet
        try:
            limit_clause = "" if self.process_all else f" LIMIT {self.batch_size}"
            with self.db._get_connection() as conn:
                # Use COALESCE to handle cases where created_at might be NULL, fallback to scraped_at
                # Only analyze jobs that haven't been analyzed yet (analyzed = 0 or NULL)
                # This ensures we always start with the most recently added jobs to the database
                query = f"""
                SELECT job_id, title, company 
                FROM jobs 
                WHERE {self.db.ACTIVE_JOBS_WHERE} AND COALESCE(analyzed, 0) = 0
                ORDER BY COALESCE(created_at, scraped_at) DESC{limit_clause}
                """
                rows = conn.execute(query).fetchall()
                
                if not rows:
                    logger.info("No unanalyzed jobs found for processing")
                    return {"job_ids_to_purge": [], "jobs_analyzed": 0, "batches_processed": 0}
                
                jobs = [JobEntry(str(job_id), str(title) if title else "", str(company) if company else "") for job_id, title, company in rows]
        except Exception as e:
            logger.error(f"Database error: {e}")
            return None
        
        # Split into batches and process
        all_purge_ids = []
        all_decisions: List[Dict[str, Any]] = []
        batch_size = self.batch_size
        batches = [jobs[i:i + batch_size] for i in range(0, len(jobs), batch_size)]
        
        # Limit to first batch if not processing all and not in test mode
        if not self.process_all and not self.test_mode and len(batches) > 1:
            batches = batches[:1]
        
        batches_processed = 0
        for batch_num, batch in enumerate(batches, 1):
            try:
                # Format jobs for LLM using compact numeric identifiers
                reference_map: Dict[str, JobEntry] = {}
                jobs_data: List[Dict[str, str]] = []
                for index, job in enumerate(batch, start=1):
                    ref_id = str(index)
                    reference_map[ref_id] = job
                    jobs_data.append({
                        "id": ref_id,
                        "title": job.title,
                        "company": job.company
                    })
                prompt = f"{self.purge_prompt}\n\nJOBS DATA:\n{json.dumps(jobs_data, indent=2)}\n\nAnalyze these jobs and return which ones should be purged."

                # Minimal logging to show what is being sent to the AI
                prompt_preview = prompt if len(prompt) <= 1200 else f"{prompt[:1200]}... (truncated)"
                logger.info(
                    "Sending batch {} with {} jobs to AI. Payload preview:\n{}",
                    batch_num,
                    len(jobs_data),
                    prompt_preview,
                )

                batch_entity_id = (
                    f"purge-batch:{batch_num}:{batch[0].id}:{batch[-1].id}"
                    if batch
                    else f"purge-batch:{batch_num}"
                )
                batch_metadata = {
                    "batch_num": batch_num,
                    "batch_size": len(batch),
                    "job_ids": [job.id for job in batch],
                    "reference_ids": [item["id"] for item in jobs_data],
                }

                result = self.llm.parse_structured(
                    response_model=AIPurger_JSON_CLASS,
                    input=f"JOBS DATA:\n{json.dumps(jobs_data, ensure_ascii=False, indent=2)}",
                    instructions=self.purge_prompt.strip(),
                )
                telemetry = result.telemetry
                self.db.record_llm_attempt(
                    task_type="purge",
                    entity_id=batch_entity_id,
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
                    metadata=batch_metadata,
                )

                if result.parsed is None:
                    if telemetry.status == "refusal":
                        logger.warning(
                            "AI purge batch {} refused: {}",
                            batch_num,
                            telemetry.refusal_text or "no refusal text",
                        )
                    else:
                        logger.error(
                            "AI purge batch {} failed: {}",
                            batch_num,
                            telemetry.error_message or telemetry.error_type or "unknown error",
                        )
                    continue

                analysis = result.parsed
                
                batches_processed += 1
                
                batch_job_ids = {job.id for job in batch}
                valid_decisions = self._normalize_candidate_payload(analysis, reference_map, batch_job_ids)

                valid_ids = [item["job_id"] for item in valid_decisions]
                
                # Test mode: print candidates
                if self.test_mode and valid_ids:
                    job_lookup = {job.id: job for job in batch}
                    logger.info(f"TEST MODE - Jobs to purge ({len(valid_ids)}):")
                    for job in valid_decisions[:5]:
                        current = job_lookup.get(job["job_id"])
                        if current:
                            logger.info(
                                "  {} @ {} | reason: {} | confidence: {:.2f}",
                                current.title,
                                current.company,
                                job.get("reason"),
                                job.get("confidence"),
                            )
                
                all_purge_ids.extend(valid_ids)
                all_decisions.extend(valid_decisions)
                
                # Mark all jobs in this batch as analyzed (both purged and kept)
                batch_job_ids_list = list(batch_job_ids)
                self._mark_jobs_as_analyzed(batch_job_ids_list)
                
                # Small delay between batches
                if batch_num < len(batches):
                    import time
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"Batch {batch_num} processing error: {e}")
                continue

        # Apply conservative global caps after all batches have been analyzed
        if (self.max_jobs > 0 or self.max_ratio > 0.0) and all_decisions:
            all_decisions.sort(key=lambda item: item.get("confidence", 0.0), reverse=True)
            cap_by_ratio = 0
            if self.max_ratio > 0 and jobs:
                cap_by_ratio = max(1, int(len(jobs) * self.max_ratio))
            cap = cap_by_ratio if cap_by_ratio > 0 else len(all_decisions)
            cap = min(cap, len(all_decisions))
            if self.max_jobs > 0:
                cap = min(cap, self.max_jobs)
            if cap < len(all_decisions):
                dropped = len(all_decisions) - cap
                logger.warning(
                    "AI purge cap applied: keeping top {} of {} candidates (dropped {}).",
                    cap,
                    len(all_decisions),
                    dropped,
                )
                all_decisions = all_decisions[:cap]
                all_purge_ids = [item["job_id"] for item in all_decisions]
        
        return {
            "job_ids_to_purge": all_purge_ids,
            "jobs_analyzed": len(jobs),
            "batches_processed": batches_processed,
            "purge_decisions": all_decisions,
        }

    def _mark_jobs_as_analyzed(self, job_ids: List[str]) -> None:
        """
        Mark jobs as analyzed in the database to avoid re-processing them
        """
        if not job_ids:
            return
            
        try:
            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                
                # Update analyzed flag to 1 for the given job IDs
                placeholders = ','.join(['?' for _ in job_ids])
                update_query = f"UPDATE jobs SET analyzed = 1 WHERE job_id IN ({placeholders})"
                
                cursor.execute(update_query, job_ids)
                conn.commit()
                
                logger.debug(f"Marked {len(job_ids)} jobs as analyzed")
                
        except Exception as e:
            logger.error(f"Error marking jobs as analyzed: {e}")

    def reset_analyzed_flags(self, all_jobs: bool = False) -> int:
        """
        Reset analyzed flags to allow re-analysis
        
        Args:
            all_jobs: If True, reset all jobs. If False, reset analyzed jobs only (default)
        
        Returns:
            Number of jobs reset
        """
        try:
            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                
                if all_jobs:
                    cursor.execute("UPDATE jobs SET analyzed = 0")
                else:
                    cursor.execute("UPDATE jobs SET analyzed = 0 WHERE analyzed = 1")
                
                conn.commit()
                reset_count = cursor.rowcount
                
                logger.info(f"Reset analyzed flag for {reset_count} jobs")
                return reset_count
                
        except Exception as e:
            logger.error(f"Error resetting analyzed flags: {e}")
            return 0

    def get_analysis_status(self) -> Dict[str, int]:
        """
        Get counts of analyzed vs unanalyzed jobs
        
        Returns:
            Dictionary with analyzed, unanalyzed, and total counts
        """
        try:
            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                
                # Get analyzed count
                analyzed_count = cursor.execute(
                    f"SELECT COUNT(*) FROM jobs WHERE {self.db.ACTIVE_JOBS_WHERE} AND analyzed = 1"
                ).fetchone()[0]
                
                # Get unanalyzed count
                unanalyzed_count = cursor.execute(
                    f"SELECT COUNT(*) FROM jobs WHERE {self.db.ACTIVE_JOBS_WHERE} AND COALESCE(analyzed, 0) = 0"
                ).fetchone()[0]
                
                # Get total count
                total_count = cursor.execute(
                    f"SELECT COUNT(*) FROM jobs WHERE {self.db.ACTIVE_JOBS_WHERE}"
                ).fetchone()[0]
                
                return {
                    "analyzed": analyzed_count,
                    "unanalyzed": unanalyzed_count,
                    "total": total_count
                }
                
        except Exception as e:
            logger.error(f"Error getting analysis status: {e}")
            return {"analyzed": 0, "unanalyzed": 0, "total": 0}

    def archive_jobs_by_ids(self, job_ids: List[str]) -> int:
        """
        Archive jobs from database by their IDs.
        """
        if not job_ids:
            logger.info("No job IDs to archive")
            return 0
            
        logger.info(f"Archiving {len(job_ids)} jobs from database")
        
        try:
            archived_count = self.db.archive_jobs(job_ids, archived_reason="Archived by AI review")
            logger.info(f"Successfully archived {archived_count} jobs in database")
            return archived_count
        except Exception as e:
            logger.error(f"Error archiving jobs in database: {e}")
            return 0

    def run_purge_mode(self) -> Dict[str, Any]:
        """
        Execute the complete AI purge process:
        1. Analyze jobs and get purge candidates
        2. Purge identified jobs (skipped in test mode)
        
        Returns summary of the operation
        """
        summary = {
            "mode": "purge-test" if self.test_mode else "purge",
            "timestamp": datetime.now(UTC).isoformat(),
            "jobs_analyzed": 0,
            "batches_processed": 0,
            "jobs_to_purge": 0,
            "jobs_purged": 0,
            "jobs_archived": 0,
            "test_mode": self.test_mode,
            "llm_stats": {},
            "success": False,
            "error": None
        }
        
        try:
            # Get jobs to purge using consolidated function
            analysis_result = self.prepare()
            
            if analysis_result is None:
                summary["error"] = "LLM analysis failed"
                return summary
            
            # Extract data from analysis result
            job_ids_to_purge = analysis_result["job_ids_to_purge"]
            summary["jobs_analyzed"] = analysis_result["jobs_analyzed"]
            summary["batches_processed"] = analysis_result["batches_processed"]
                
            summary["jobs_to_purge"] = len(job_ids_to_purge)
            summary["purge_decisions"] = analysis_result.get("purge_decisions", [])
            summary["llm_stats"] = self.llm.get_stats()
            
            if not job_ids_to_purge:
                logger.info("No jobs identified for archival")
                summary["success"] = True
                return summary
            
            # Archive jobs (skip in test mode)
            if self.test_mode:
                logger.info(f"TEST MODE: Would archive {len(job_ids_to_purge)} jobs")
                summary["jobs_purged"] = 0
                summary["jobs_archived"] = 0
                summary["success"] = True
            else:
                archive_summary = self.db.archive_jobs_with_filter_decisions(
                    summary["purge_decisions"],
                    default_reason="Archived by AI review",
                )
                archived_count = int(archive_summary.get("archived", 0) or 0)
                summary["jobs_purged"] = archived_count
                summary["jobs_archived"] = archived_count
                summary["filter_decisions_recorded"] = int(archive_summary.get("decisions_recorded", 0) or 0)

                if archived_count > 0:
                    logger.info(f"Archived {archived_count} jobs")
                    summary["success"] = True
                else:
                    summary["error"] = "No jobs were actually archived"
                    summary["llm_stats"] = self.llm.get_stats()
            
            return summary
            
        except Exception as e:
            logger.error(f"AI-Purger error: {e}")
            summary["error"] = str(e)
            summary["llm_stats"] = self.llm.get_stats()
            return summary
