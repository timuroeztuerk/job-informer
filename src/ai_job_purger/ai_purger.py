from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import hashlib
import json
import sqlite3
from datetime import datetime
from loguru import logger
import requests
import pandas as pd

from ..config.settings import Config
from ..utils.database import JobDatabase
from ..description_parser.parser import DescriptionParser

@dataclass
class JobEntry:
    """Job entry for AI analysis"""
    id: str
    title: str
    company: str

class AIPurger:
    """
    AI Job Purger class
    Removes unwanted job postings using LLMs with caching.
    """

    def __init__(self, config: Config, test_mode: bool = False):
        self.config = config
        self.test_mode = test_mode
        self.db = JobDatabase()
        self.parser = DescriptionParser(config=self.config,db=self.db)
        self.parser._ensure_table()
        
        # LLM API setup (using same Gemini configuration as existing modules)
        self.model = getattr(self.config, 'gemini_model', 'gemini-2.5-pro')
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        self.headers = {'Content-Type': 'application/json'}
        
        # Batch processing configuration
        self.batch_size = 50
        
        # Placeholder for the PURGE_AI prompt (empty for now as requested)
        self.purge_prompt = """
        You are an AI job filter. Analyze the provided job listings and identify jobs that should be purged.
        
        IMPORTANT: Be conservative and only purge jobs that are clearly irrelevant or low-quality.
        Looking at the job titles and companies, you should purge jobs that are:
        - Clearly IRRELEVANT to data science, machine learning, AI, or software engineering (e.g. sales, retail, manual labor)
        - Obviously spam, duplicate, or very low-quality postings
        - Jobs from companies in the unwanted list
        - Jobs with titles containing unwanted keywords
        - AI consultant jobs can stay, if they are not duplicate.
        DO NOT purge jobs that might be relevant, even if you're unsure.
        
        Return only the job IDs that should be purged as a JSON array.
        Example: ["job_id_1", "job_id_2"]
        """

    def get_all_jobs(self) -> List[JobEntry]:
        """
        Collect id, title, and company information from database as a list
        Returns list of JobEntry objects
        """
        logger.info("Retrieving all jobs from database for AI analysis")
        
        try:
            with sqlite3.connect(self.db.db_path) as conn:
                query = "SELECT job_id, title, company FROM jobs ORDER BY created_at DESC"
                cursor = conn.execute(query)
                rows = cursor.fetchall()
                
                jobs = []
                for row in rows:
                    job_id, title, company = row
                    jobs.append(JobEntry(
                        id=str(job_id),
                        title=str(title) if title else "",
                        company=str(company) if company else ""
                    ))
                
                logger.info(f"Retrieved {len(jobs)} jobs for AI analysis")
                return jobs
                
        except Exception as e:
            logger.error(f"Error retrieving jobs from database: {e}")
            return []

    def _split_into_batches(self, jobs: List[JobEntry], batch_size: int) -> List[List[JobEntry]]:
        """Split jobs list into smaller batches"""
        batches = []
        for i in range(0, len(jobs), batch_size):
            batch = jobs[i:i + batch_size]
            batches.append(batch)
        return batches

    def _print_purge_candidates(self, batch: List[JobEntry], purge_ids: List[str]) -> None:
        """Print detailed information about jobs selected for purging (test mode only)"""
        if not purge_ids:
            logger.info("=== TEST MODE: No jobs selected for purging ===")
            return
        
        # Create a lookup for quick access
        job_lookup = {job.id: job for job in batch}
        
        logger.info(f"=== TEST MODE: Jobs selected for purging ({len(purge_ids)}) ===")
        for i, job_id in enumerate(purge_ids, 1):
            job = job_lookup.get(job_id)
            if job:
                logger.info(f"  {i:2d}. ID: {job_id[:50]}...")
                logger.info(f"      Title: {job.title}")
                logger.info(f"      Company: {job.company}")
                logger.info(f"      {'─' * 50}")
            else:
                logger.warning(f"  {i:2d}. ID: {job_id} (NOT FOUND in batch)")
        
        logger.info(f"=== END TEST MODE RESULTS ===")

    def send_jobs_to_llm(self, jobs: List[JobEntry]) -> Optional[List[str]]:
        """
        Send job list to LLM in batches with PURGE_AI prompt
        Returns list of job IDs to be purged, or None on error
        """
        if not jobs:
            logger.warning("No jobs to send to LLM")
            return []
            
        if not getattr(self.config, 'gemini_api_key', ''):
            logger.error("GEMINI_API_KEY missing; cannot perform AI purging")
            return None
            
        # Safety check: if prompt is empty, return empty list to prevent accidental purging
        if not self.purge_prompt.strip():
            logger.warning("PURGE_AI prompt is empty - returning no jobs to purge for safety")
            return []
        
        # Split jobs into batches
        batches = self._split_into_batches(jobs, self.batch_size)
        
        if self.test_mode:
            logger.info(f"=== TEST MODE ENABLED ===")
            logger.info(f"Processing only the first batch (batch 1/1) with {len(batches[0])} jobs")
            batches = batches[:1]  # Only process the first batch
        else:
            logger.info(f"Processing {len(jobs)} jobs in {len(batches)} batches of {self.batch_size}")
        
        all_purge_ids = []
        
        for batch_num, batch in enumerate(batches, 1):
            if self.test_mode:
                logger.info(f"=== TEST MODE: Processing test batch ({len(batch)} jobs) ===")
            else:
                logger.info(f"Processing batch {batch_num}/{len(batches)} ({len(batch)} jobs)")
            
            batch_purge_ids = self._process_batch(batch, batch_num)
            
            if batch_purge_ids is None:
                logger.error(f"Failed to process batch {batch_num} - aborting")
                return None
            
            # In test mode, print detailed information about jobs to be purged
            if self.test_mode and batch_purge_ids:
                logger.info(f"=== TEST MODE: AI identified {len(batch_purge_ids)} jobs for purging ===")
                self._print_purge_candidates(batch, batch_purge_ids)
            
            all_purge_ids.extend(batch_purge_ids)
            logger.info(f"Batch {batch_num} identified {len(batch_purge_ids)} jobs for purging ({len(batch_purge_ids)/len(batch)*100:.1f}%)")
            
            # Small delay between batches to be respectful to API
            if batch_num < len(batches):
                import time
                time.sleep(1)
        
        logger.info(f"LLM identified {len(all_purge_ids)} jobs for purging across all batches")
        return all_purge_ids

    def _process_batch(self, batch: List[JobEntry], batch_num: int) -> Optional[List[str]]:
        """
        Process a single batch of jobs through the LLM
        Returns list of job IDs to purge for this batch, or None on error
        """
        # Format jobs for LLM
        jobs_data = []
        for job in batch:
            jobs_data.append({
                "id": job.id,
                "title": job.title,
                "company": job.company
            })
        
        # Create prompt
        prompt = f"{self.purge_prompt}\n\nJOBS DATA (Batch {batch_num}):\n{json.dumps(jobs_data, indent=2)}\n\nPlease respond with a JSON array of job IDs to purge from this batch: [\"id1\", \"id2\", ...]\n\nIMPORTANT: Only return IDs that are actually in the above list."
        
        # Debug: log sample job IDs from this batch
        sample_ids = [job.id[:50] + "..." if len(job.id) > 50 else job.id for job in batch[:3]]
        logger.debug(f"Batch {batch_num} sample job IDs: {sample_ids}")
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ]
        }
        
        try:
            url = f"{self.api_url}?key={self.config.gemini_api_key}"
            resp = requests.post(url, headers=self.headers, json=payload, timeout=90)  # Increased timeout
            
            if resp.status_code != 200:
                logger.error(f"Gemini API error for batch {batch_num}: {resp.status_code} - {resp.text[:200]}")
                return None
                
            data = resp.json()
            
            # Extract text from response
            text = None
            try:
                text = data['candidates'][0]['content']['parts'][0]['text']
            except Exception as e:
                logger.error(f"Error extracting text from LLM response for batch {batch_num}: {e}")
                return None
                
            if not text:
                logger.warning(f"Empty response from LLM for batch {batch_num}")
                return []
                
            # Parse JSON response
            text_stripped = text.strip()
            
            # Handle code fences
            if text_stripped.startswith('```'):
                text_stripped = text_stripped.strip('`')
                text_stripped = text_stripped.replace('json', '', 1).strip()
            
            try:
                purge_ids = json.loads(text_stripped)
                
                # Ensure it's a list
                if not isinstance(purge_ids, list):
                    logger.error(f"LLM response for batch {batch_num} is not a list of IDs")
                    return None
                    
                # Convert all items to strings and validate they exist in this batch
                batch_job_ids = {job.id for job in batch}
                valid_purge_ids = []
                invalid_count = 0
                
                for id_val in purge_ids:
                    id_str = str(id_val)
                    if id_str in batch_job_ids:
                        valid_purge_ids.append(id_str)
                    else:
                        invalid_count += 1
                        if invalid_count <= 3:  # Only log first 3 invalid IDs to avoid spam
                            logger.warning(f"LLM returned job ID '{id_str[:100]}...' not present in batch {batch_num} - ignoring")
                
                if invalid_count > 3:
                    logger.warning(f"LLM returned {invalid_count - 3} additional invalid job IDs for batch {batch_num} (not logged)")
                
                if invalid_count > len(valid_purge_ids):
                    logger.error(f"Batch {batch_num}: LLM returned more invalid IDs ({invalid_count}) than valid ones ({len(valid_purge_ids)}) - this suggests a problem with the prompt or data format")
                
                return valid_purge_ids
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from LLM response for batch {batch_num}: {e}")
                logger.debug(f"Raw LLM response for batch {batch_num}: {text_stripped[:500]}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"LLM request error for batch {batch_num}: {e}")
            return None

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
        1. Connect to database
        2. Collect job data (id, title, company)
        3. Send to LLM for analysis
        4. Purge identified jobs (skipped in test mode)
        
        Returns summary of the operation
        """
        if self.test_mode:
            logger.info("Starting AI-Purger mode in TEST MODE")
        else:
            logger.info("Starting AI-Purger mode")
        
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
            # Step 1 & 2: Connect to database and collect jobs
            jobs = self.get_all_jobs()
            summary["jobs_analyzed"] = len(jobs)
            
            if not jobs:
                logger.warning("No jobs found in database")
                summary["success"] = True
                return summary
            
            # Calculate number of batches
            if self.test_mode:
                summary["batches_processed"] = 1
                logger.info(f"TEST MODE: Will process only first batch (50 jobs out of {len(jobs)} total)")
            else:
                num_batches = (len(jobs) + self.batch_size - 1) // self.batch_size
                summary["batches_processed"] = num_batches
                logger.info(f"Will process {len(jobs)} jobs in {num_batches} batches of {self.batch_size}")
            
            # Step 3: Send to LLM for analysis
            job_ids_to_purge = self.send_jobs_to_llm(jobs)
            
            if job_ids_to_purge is None:
                summary["error"] = "LLM analysis failed"
                return summary
                
            summary["jobs_to_purge"] = len(job_ids_to_purge)
            
            if not job_ids_to_purge:
                if self.test_mode:
                    logger.info("TEST MODE: LLM identified no jobs for purging")
                else:
                    logger.info("LLM identified no jobs for purging")
                summary["success"] = True
                return summary
            
            # Step 4: Purge identified jobs (skip in test mode)
            if self.test_mode:
                logger.info(f"=== TEST MODE: Skipping actual purging of {len(job_ids_to_purge)} jobs ===")
                logger.info("TEST MODE: In production mode, these jobs would be deleted from the database")
                summary["jobs_purged"] = 0  # No actual purging in test mode
                summary["success"] = True
            else:
                purged_count = self.purge_jobs_by_ids(job_ids_to_purge)
                summary["jobs_purged"] = purged_count
                
                if purged_count > 0:
                    logger.info(f"AI-Purger completed successfully: analyzed {len(jobs)} jobs, purged {purged_count} jobs")
                    summary["success"] = True
                else:
                    summary["error"] = "No jobs were actually purged"
            
            return summary
            
        except Exception as e:
            logger.error(f"Unexpected error in AI-Purger mode: {e}")
            summary["error"] = str(e)
            return summary

    def llm_connection(self) -> bool:
        """
        Test LLM connection
        Returns True if connection is successful, False otherwise
        """
        try:
            if not getattr(self.config, 'gemini_api_key', ''):
                logger.error("GEMINI_API_KEY not configured")
                return False
                
            # Simple test call to check connection
            test_payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": "Hello, respond with just 'OK' if you can read this."}
                        ]
                    }
                ]
            }
            
            url = f"{self.api_url}?key={self.config.gemini_api_key}"
            resp = requests.post(url, headers=self.headers, json=test_payload, timeout=15)
            
            if resp.status_code == 200:
                logger.info("LLM connection test successful")
                return True
            else:
                logger.error(f"LLM connection test failed: {resp.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"LLM connection test error: {e}")
            return False
        