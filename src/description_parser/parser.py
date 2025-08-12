"""
Description Parser
- Incremental, cached parsing of job descriptions into structured JSON
- Uses Gemini API (same key/model as market_analyst by default)
- Writes results into a separate SQLite table to avoid touching jobs.db schema
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import hashlib
import json
import sqlite3
from datetime import datetime

import pandas as pd
import requests
from loguru import logger

from ..config.settings import Config
from ..utils.database import JobDatabase


@dataclass
class ParsedDescription:
    job_id: str
    desc_hash: str
    version: int
    model: str
    payload_json: str  # raw JSON string from LLM
    created_at: str


class DescriptionParser:
    """LLM-backed parser with caching based on (job_id, desc_hash, version)."""

    def __init__(self, config: Config, db: Optional[JobDatabase] = None):
        self.config = config
        self.db = db or JobDatabase()
        self._ensure_table()
        # API setup (Gemini-compatible default)
        self.model = getattr(self.config, 'desc_parser_model', getattr(self.config, 'gemini_model', 'gemini-2.5-flash'))
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        self.headers = {'Content-Type': 'application/json'}
        self.prompt = getattr(self.config, 'desc_parser_prompt', '')

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

    def _call_llm(self, description_text: str) -> Optional[str]:
        """Call Gemini API; return raw JSON text or None."""
        if not description_text.strip():
            return None
        if not getattr(self.config, 'gemini_api_key', ''):
            logger.error("GEMINI_API_KEY missing; cannot parse descriptions")
            return None
        prompt = f"{self.prompt}\n\nDESCRIPTION:\n{description_text.strip()}\n\nRespond with JSON only."
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
            resp = requests.post(url, headers=self.headers, json=payload, timeout=30)
            if resp.status_code != 200:
                logger.error(f"Gemini API error: {resp.status_code} - {resp.text[:200]}")
                return None
            data = resp.json()
            # Extract text field
            text = None
            try:
                text = data['candidates'][0]['content']['parts'][0]['text']
            except Exception:
                pass
            if not text:
                logger.warning("Unexpected Gemini response format")
                return None
            # If the model wrapped JSON in code fences or prose, try to locate JSON block
            text_stripped = text.strip()
            # simple heuristics
            if text_stripped.startswith('```'):
                text_stripped = text_stripped.strip('`')
                # remove possible json language hint
                text_stripped = text_stripped.replace('json', '', 1).strip()
            # Validate JSON
            try:
                _ = json.loads(text_stripped)
                return text_stripped
            except json.JSONDecodeError:
                # Try to find first {...} block
                import re
                m = re.search(r"\{[\s\S]*\}", text_stripped)
                if m:
                    candidate = m.group(0)
                    try:
                        _ = json.loads(candidate)
                        return candidate
                    except Exception:
                        pass
                logger.error("Failed to parse JSON from LLM output")
                return None
        except requests.RequestException as e:
            logger.error(f"LLM request error: {e}")
            return None

    def parse_batch(self, jobs_df: pd.DataFrame) -> int:
        """Parse a batch of jobs with non-empty descriptions and no cache hit.
        Returns number of newly stored parses.
        """
        if jobs_df.empty:
            return 0
        version = int(getattr(self.config, 'desc_parser_version', 1))
        dry = bool(getattr(self.config, 'desc_parser_dry_run', False))
        created_ts = datetime.utcnow().isoformat()

        new_count = 0
        for _, row in jobs_df.iterrows():
            desc = str(row.get('description') or '').strip()
            job_id = str(row.get('job_id') or '')
            if not desc or not job_id:
                continue
            h = self._hash_description(desc)
            cached = self._find_cached(job_id, h, version)
            if cached:
                continue
            if dry:
                # store a placeholder minimal payload without calling LLM
                payload = json.dumps({"dry_run": True})
            else:
                payload = self._call_llm(desc)
                if not payload:
                    continue
            rec = ParsedDescription(
                job_id=job_id,
                desc_hash=h,
                version=version,
                model=self.model,
                payload_json=payload,
                created_at=created_ts,
            )
            self._store(rec)
            new_count += 1
        return new_count

    def run_incremental(self, batch_size: int, max_batches: int) -> int:
        """Find jobs with descriptions and without cached parse, scanning with OFFSET to avoid stopping at cached windows.
        Caps total new parses to (batch_size * max_batches).
        """
        total_new = 0
        total_seen = 0
        total_skipped_cached = 0
        version = int(getattr(self.config, 'desc_parser_version', 1))
        window_size = max(1, batch_size * 5)  # scan a larger window to find uncached items
        max_new = batch_size * max_batches
        offset = 0

        with sqlite3.connect(self.db.db_path) as conn:
            for window_index in range(max_batches):
                # Pull a window of jobs with non-empty descriptions
                df = pd.read_sql_query(
                    """
                    SELECT job_id, description
                    FROM jobs
                    WHERE description IS NOT NULL AND TRIM(description) <> ''
                    ORDER BY datetime(COALESCE(scraped_at, created_at)) DESC
                    LIMIT ? OFFSET ?
                    """,
                    conn,
                    params=[window_size, offset],
                )
                if df.empty:
                    break
                df['desc_hash'] = df['description'].astype(str).apply(self._hash_description)

                # Load existing cache keys for these job_ids at this version
                keys = tuple(set(df['job_id'].astype(str).tolist()))
                if not keys:
                    offset += window_size
                    continue
                placeholder = ",".join(["?"] * len(keys))
                cache_df = pd.read_sql_query(
                    f"SELECT job_id, desc_hash FROM parsed_descriptions WHERE version = ? AND job_id IN ({placeholder})",
                    conn,
                    params=[version, *keys],
                )
                cache_set = set((str(r['job_id']), str(r['desc_hash'])) for _, r in cache_df.iterrows()) if not cache_df.empty else set()

                mask = [
                    (str(r['job_id']), str(r['desc_hash'])) not in cache_set for _, r in df.iterrows()
                ]
                candidates = df[mask]
                batch_seen = int(len(df))
                batch_to_process = int(len(candidates))
                batch_cached = batch_seen - batch_to_process
                total_seen += batch_seen
                total_skipped_cached += batch_cached

                if candidates.empty:
                    logger.info("Description parser: cache hit for all candidates in this window; advancing to next window")
                    offset += window_size
                    continue

                # Respect cap on total new parses this run
                remaining_quota = max(0, max_new - total_new)
                if remaining_quota <= 0:
                    break

                # Process up to the allowed quota; drop helper column
                to_parse = candidates.head(min(batch_size, remaining_quota)).drop(columns=['desc_hash'])
                processed = self.parse_batch(to_parse)
                total_new += processed

                # Move to next window
                offset += window_size

                # Stop early if we reached the quota
                if total_new >= max_new:
                    break

        if total_seen > 0:
            logger.info(
                f"Description parser summary — scanned:{total_seen} cached_skipped:{total_skipped_cached} stored_new:{total_new} version:{version}"
            )
        else:
            logger.info("Description parser summary — no rows with descriptions found")
        return total_new
