from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import json
import sqlite3
from datetime import datetime
from loguru import logger
import requests
from pydantic import BaseModel

from ..config.settings import Config
from ..utils.database import JobDatabase
from .parser import DescriptionTools
from ..utils.llm_connection import LLMConnection

@dataclass
class JobEntry:
    """Job entry for AI analysis"""
    id: str
    title: str
    company: str

class AIPurger_JSON_CLASS(BaseModel):
    """Structured response model for AI purger"""
    job_ids_to_purge: List[str]

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

    def __init__(self, config: Config, test_mode: bool = False, process_all: bool = True):
        self.config = config
        self.test_mode = test_mode
        self.process_all = process_all  # If True, processes all jobs; if False, processes only default batch size
        self.db = JobDatabase()
        self.parser = DescriptionTools(config=self.config, db=self.db)
        self.parser._ensure_table()
        self.llm = LLMConnection(model="gpt-5-nano")
        self.batch_size = 25
        self.purge_prompt = """
        You are an AI job filter. Analyze the provided job listings and identify jobs that should be purged.
        
        IMPORTANT: Be conservative and only purge jobs that are clearly irrelevant or low-quality.
        Looking at the job titles and companies, you should purge jobs that are:
        - Clearly IRRELEVANT to data science, data analysis, machine learning, AI, or software engineering (e.g. sales, retail, manual labor).
        - I'm looking for data science, machine learning, AI jobs, NOT purely software engineering jobs.
        - Obviously spam, duplicate, or very low-quality postings.
        - AI consultant jobs can stay, if they are not duplicate.
        DO NOT purge jobs that might be relevant, even if you're unsure.
        
        Return only the job IDs that should be purged as a JSON array.
        Example: ["job_id_1", "job_id_2"]
        """
        self.json_structure = AIPurger_JSON_CLASS

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
            with sqlite3.connect(self.db.db_path) as conn:
                # Use COALESCE to handle cases where created_at might be NULL, fallback to scraped_at
                # Only analyze jobs that haven't been analyzed yet (analyzed = 0 or NULL)
                # This ensures we always start with the most recently added jobs to the database
                query = f"""
                SELECT job_id, title, company 
                FROM jobs 
                WHERE COALESCE(analyzed, 0) = 0
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
        batch_size = self.batch_size
        batches = [jobs[i:i + batch_size] for i in range(0, len(jobs), batch_size)]
        
        # Limit to first batch if not processing all and not in test mode
        if not self.process_all and not self.test_mode and len(batches) > 1:
            batches = batches[:1]
        
        batches_processed = 0
        for batch_num, batch in enumerate(batches, 1):
            try:
                # Format jobs for LLM
                jobs_data = [{"id": job.id, "title": job.title, "company": job.company} for job in batch]
                prompt = f"{self.purge_prompt}\n\nJOBS DATA:\n{json.dumps(jobs_data, indent=2)}\n\nAnalyze these jobs and return which ones should be purged."
                
                # Get structured LLM response
                analysis_result = self.llm.generate_structured(prompt, AIPurger_JSON_CLASS)
                if not analysis_result:
                    continue
                
                # Handle both parsed object and JSON string responses
                if isinstance(analysis_result, str):
                    try:
                        analysis = AIPurger_JSON_CLASS.model_validate_json(analysis_result)
                    except Exception as parse_error:
                        logger.error(f"Failed to parse JSON: {parse_error}")
                        continue
                else:
                    # Assume it's already a parsed object
                    analysis = analysis_result
                
                batches_processed += 1
                
                # Validate IDs exist in batch
                batch_job_ids = {job.id for job in batch}
                valid_ids = [job_id for job_id in analysis.job_ids_to_purge if job_id in batch_job_ids]
                
                # Test mode: print candidates
                if self.test_mode and valid_ids:
                    job_lookup = {job.id: job for job in batch}
                    logger.info(f"TEST MODE - Jobs to purge ({len(valid_ids)}):")
                    for job_id in valid_ids[:5]:  # Show max 5
                        job = job_lookup.get(job_id)
                        if job:
                            logger.info(f"  {job.title} @ {job.company}")
                
                all_purge_ids.extend(valid_ids)
                
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
        
        return {
            "job_ids_to_purge": all_purge_ids,
            "jobs_analyzed": len(jobs),
            "batches_processed": batches_processed
        }

    def _mark_jobs_as_analyzed(self, job_ids: List[str]) -> None:
        """
        Mark jobs as analyzed in the database to avoid re-processing them
        """
        if not job_ids:
            return
            
        try:
            with sqlite3.connect(self.db.db_path) as conn:
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
            all_jobs: If True, reset all jobs. If False, reset only unanalyzed jobs (default)
        
        Returns:
            Number of jobs reset
        """
        try:
            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                
                if all_jobs:
                    cursor.execute("UPDATE jobs SET analyzed = 0")
                else:
                    cursor.execute("UPDATE jobs SET analyzed = 0 WHERE analyzed IS NULL OR analyzed != 1")
                
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
            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                
                # Get analyzed count
                analyzed_count = cursor.execute("SELECT COUNT(*) FROM jobs WHERE analyzed = 1").fetchone()[0]
                
                # Get unanalyzed count
                unanalyzed_count = cursor.execute("SELECT COUNT(*) FROM jobs WHERE COALESCE(analyzed, 0) = 0").fetchone()[0]
                
                # Get total count
                total_count = cursor.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                
                return {
                    "analyzed": analyzed_count,
                    "unanalyzed": unanalyzed_count,
                    "total": total_count
                }
                
        except Exception as e:
            logger.error(f"Error getting analysis status: {e}")
            return {"analyzed": 0, "unanalyzed": 0, "total": 0}

    def purge_jobs_by_ids(self, job_ids: List[str]) -> int:
        """
        Purge jobs from database by their IDs
        Returns count of jobs successfully deleted
        """
        if not job_ids:
            logger.info("No job IDs to purge")
            return 0
            
        logger.info(f"Purging {len(job_ids)} jobs from database")
        
        try:
            with sqlite3.connect(self.db.db_path) as conn:
                cursor = conn.cursor()
                
                # Delete jobs by IDs
                placeholders = ','.join(['?' for _ in job_ids])
                delete_query = f"DELETE FROM jobs WHERE job_id IN ({placeholders})"
                
                cursor.execute(delete_query, job_ids)
                conn.commit()
                
                deleted_count = cursor.rowcount
                logger.info(f"Successfully purged {deleted_count} jobs from database")
                
                return deleted_count
                
        except Exception as e:
            logger.error(f"Error purging jobs from database: {e}")
            return 0

    def run_purge_mode(self) -> Dict[str, Any]:
        """
        Execute the complete AI purge process:
        1. Analyze jobs and get purge candidates
        2. Purge identified jobs (skipped in test mode)
        
        Returns summary of the operation
        """
        summary = {
            "mode": "ai-purge-test" if self.test_mode else "ai-purge",
            "timestamp": datetime.now().isoformat(),
            "jobs_analyzed": 0,
            "batches_processed": 0,
            "jobs_to_purge": 0,
            "jobs_purged": 0,
            "test_mode": self.test_mode,
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
            
            if not job_ids_to_purge:
                logger.info("No jobs identified for purging")
                summary["success"] = True
                return summary
            
            # Purge jobs (skip in test mode)
            if self.test_mode:
                logger.info(f"TEST MODE: Would purge {len(job_ids_to_purge)} jobs")
                summary["jobs_purged"] = 0
                summary["success"] = True
            else:
                purged_count = self.purge_jobs_by_ids(job_ids_to_purge)
                summary["jobs_purged"] = purged_count
                
                if purged_count > 0:
                    logger.info(f"Purged {purged_count} jobs")
                    summary["success"] = True
                else:
                    summary["error"] = "No jobs were actually purged"
            
            return summary
            
        except Exception as e:
            logger.error(f"AI-Purger error: {e}")
            summary["error"] = str(e)
            return summary