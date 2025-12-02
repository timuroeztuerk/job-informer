"""
Database utilities for job storage using SQLite
Provides fast deduplication and querying capabilities
"""

import os
import sqlite3
import pandas as pd
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from loguru import logger
import numpy as np

# Default DB relative to backend directory unless overridden by env
DEFAULT_DB_PATH = os.getenv(
    "JOBS_DB_PATH",
    str(Path(__file__).resolve().parents[2] / "data" / "jobs.db"),
)


class JobDatabase:
    """SQLite-based job storage with fast deduplication and querying"""
    
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_data_dir()
        self._init_db()
    
    def _ensure_data_dir(self):
        """Ensure data directory exists"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
    
    def _init_db(self):
        """Initialize database with schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL,
                    source TEXT NOT NULL,
                    url TEXT,
                    salary TEXT,
                    description TEXT,
                    normalized_key TEXT,
                    scraped_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create indexes for fast querying
            conn.execute("CREATE INDEX IF NOT EXISTS idx_company ON jobs(company)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON jobs(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scraped_at ON jobs(scraped_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON jobs(created_at)")
            
            # Backfill: ensure description/normalized_key/analyzed columns exist for older databases
            try:
                cur = conn.execute("PRAGMA table_info(jobs)")
                columns = {row[1] for row in cur.fetchall()}
                if 'description' not in columns:
                    conn.execute("ALTER TABLE jobs ADD COLUMN description TEXT")
                if 'normalized_key' not in columns:
                    conn.execute("ALTER TABLE jobs ADD COLUMN normalized_key TEXT")
                if 'analyzed' not in columns:
                    conn.execute("ALTER TABLE jobs ADD COLUMN analyzed INTEGER DEFAULT 0")
            except Exception:
                # If PRAGMA or ALTER fails, proceed without blocking app
                pass

            # Try to create unique index on normalized_key (may fail if duplicates exist)
            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_key ON jobs(normalized_key)")
            except Exception as e:
                logger.warning(f"Could not create unique index on normalized_key (possible duplicates present): {e}")

            conn.commit()
    
    def _get_connection(self):
        """Get database connection"""
        return sqlite3.connect(self.db_path)
    
    def put_into_sql(self, jobs_df: pd.DataFrame) -> int:
        """Insert new jobs, ignore duplicates. Returns count of new jobs inserted."""
        if jobs_df.empty:
            return 0
        
        # Ensure job_id column exists
        if 'job_id' not in jobs_df.columns:
            logger.warning("No job_id column found in DataFrame")
            return 0
        
        # Prepare data for insertion
        jobs_to_insert = []
        for _, row in jobs_df.iterrows():
            # Build normalized_key similar to build_normalized_keys for a single row
            url = str(row.get('url', '')).strip()
            source = str(row.get('source', '')).strip()
            if url:
                normalized_key = str(url)
            else:
                title = str(row.get('title', '')).strip().lower()
                company = str(row.get('company', '')).strip().lower()
                normalized_key = f"{source.lower()}|{title}|{company}"
            job_data = {
                'job_id': str(row['job_id']),
                'title': str(row['title']),
                'company': str(row['company']),
                'location': str(row['location']),
                'source': str(row['source']),
                'url': str(row.get('url', '')),
                'salary': str(row.get('salary', 'Not specified')),
                'description': str(row.get('description', '')),
                'normalized_key': normalized_key,
                'scraped_at': pd.Timestamp.now().isoformat()
            }
            jobs_to_insert.append(job_data)
        
        # Insert with conflict resolution
        inserted_count = 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            for job in jobs_to_insert:
                try:
                    cursor.execute("""
                        INSERT INTO jobs (job_id, title, company, location, source, url, salary, description, normalized_key, scraped_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        job['job_id'], job['title'], job['company'], job['location'],
                        job['source'], job['url'], job['salary'], job['description'], job['normalized_key'], job['scraped_at']
                    ))
                    inserted_count += 1
                except sqlite3.IntegrityError:
                    # Job already exists (PRIMARY KEY constraint)
                    pass
            
            conn.commit()

        logger.info(f"DB Updated with {inserted_count} new jobs.")
        return inserted_count
    
    def get_recent_jobs(self, days: int = 30) -> pd.DataFrame:
        """Get jobs from last N days"""
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("""
                SELECT * FROM jobs 
                WHERE scraped_at > ?
                ORDER BY scraped_at DESC
            """, conn, params=[cutoff_date])
        
        if not df.empty and 'scraped_at' in df.columns:
            df['scraped_at'] = pd.to_datetime(df['scraped_at'])
        
        logger.info(f"Retrieved {len(df)} jobs from last {days} days")
        return df
    
    def get_jobs_by_company(self, company: str) -> pd.DataFrame:
        """Get all jobs from specific company"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("""
                SELECT * FROM jobs 
                WHERE company LIKE ?
                ORDER BY scraped_at DESC
            """, conn, params=[f'%{company}%'])
        
        if not df.empty and 'scraped_at' in df.columns:
            df['scraped_at'] = pd.to_datetime(df['scraped_at'])
        
        logger.info(f"Retrieved {len(df)} jobs for company '{company}'")
        return df
    
    def get_jobs_by_source(self, source: str) -> pd.DataFrame:
        """Get all jobs from specific source"""
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("""
                SELECT * FROM jobs 
                WHERE source = ?
                ORDER BY scraped_at DESC
            """, conn, params=[source])
        
        if not df.empty and 'scraped_at' in df.columns:
            df['scraped_at'] = pd.to_datetime(df['scraped_at'])
        
        logger.info(f"Retrieved {len(df)} jobs from source '{source}'")
        return df
    
    def get_job_summary(self) -> Dict:
        """Get summary statistics from database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Total jobs
            cursor.execute("SELECT COUNT(*) FROM jobs")
            total_jobs = cursor.fetchone()[0]
            
            # Jobs by source
            cursor.execute("SELECT source, COUNT(*) FROM jobs GROUP BY source")
            jobs_by_source = dict(cursor.fetchall())
            
            # Jobs by company (top 10)
            cursor.execute("""
                SELECT company, COUNT(*) as count 
                FROM jobs 
                GROUP BY company 
                ORDER BY count DESC 
                LIMIT 10
            """)
            top_companies = dict(cursor.fetchall())
            
            # Recent activity
            cursor.execute("""
                SELECT COUNT(*) FROM jobs 
                WHERE scraped_at > datetime('now', '-7 days')
            """)
            recent_jobs = cursor.fetchone()[0]
            
            # Date range
            cursor.execute("SELECT MIN(scraped_at), MAX(scraped_at) FROM jobs")
            date_range = cursor.fetchone()
            
            # Parsed descriptions statistics
            parsed_descriptions_stats = {}
            try:
                # Check if parsed_descriptions table exists
                cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='parsed_descriptions'")
                if cursor.fetchone()[0] > 0:
                    # Total parsed descriptions
                    cursor.execute("SELECT COUNT(*) FROM parsed_descriptions")
                    total_parsed = cursor.fetchone()[0]

                    # Parsed descriptions tied to current jobs (all versions)
                    cursor.execute("""
                        SELECT COUNT(*) 
                        FROM parsed_descriptions pd
                        INNER JOIN jobs j ON pd.job_id = j.job_id
                    """)
                    active_parsed = cursor.fetchone()[0]

                    # Unique jobs with parsed descriptions (current jobs only)
                    cursor.execute("""
                        SELECT COUNT(DISTINCT pd.job_id)
                        FROM parsed_descriptions pd
                        INNER JOIN jobs j ON pd.job_id = j.job_id
                    """)
                    active_jobs_parsed = cursor.fetchone()[0]

                    # Unique jobs with parsed descriptions (all, including deleted)
                    cursor.execute("SELECT COUNT(DISTINCT job_id) FROM parsed_descriptions")
                    unique_jobs_parsed = cursor.fetchone()[0]
                    
                    # Orphaned parsed descriptions (job_id not in jobs table)
                    cursor.execute("""
                        SELECT COUNT(DISTINCT pd.job_id) 
                        FROM parsed_descriptions pd 
                        LEFT JOIN jobs j ON pd.job_id = j.job_id 
                        WHERE j.job_id IS NULL
                    """)
                    orphaned_parsed = cursor.fetchone()[0]
                    
                    parsed_descriptions_stats = {
                        'total_parsed_descriptions': total_parsed,
                        'active_parsed_descriptions': active_parsed,
                        'unique_jobs_with_parsed_descriptions': unique_jobs_parsed,
                        'orphaned_parsed_descriptions': orphaned_parsed,
                        'jobs_with_descriptions': active_jobs_parsed
                    }
            except Exception as e:
                logger.warning(f"Could not get parsed descriptions stats: {e}")
            
        return {
            'total_jobs': total_jobs,
            'jobs_by_source': jobs_by_source,
            'top_companies': top_companies,
            'recent_jobs_7_days': recent_jobs,
            'date_range': {
                'earliest': date_range[0] if date_range[0] else None,
                'latest': date_range[1] if date_range[1] else None
            },
            'parsed_descriptions_stats': parsed_descriptions_stats
        }
    
    def migrate_csv_to_sqlite(self, csv_file: str) -> int:
        """Migrate jobs from CSV file to SQLite database"""
        try:
            df = pd.read_csv(csv_file)
            if 'scraped_at' in df.columns:
                df['scraped_at'] = pd.to_datetime(df['scraped_at'], errors='coerce')
            
            # Build job_ids if not present
            if 'job_id' not in df.columns:
                df = self._ensure_job_ids(df)
            
            inserted = self.put_into_sql(df)
            logger.info(f"Migrated {inserted} jobs from {csv_file}")
            return inserted
            
        except Exception as e:
            logger.error(f"Failed to migrate {csv_file}: {e}")
            return 0
    
    def export_to_csv(self, filename: Optional[str] = None) -> str:
        """Export all jobs to CSV file"""
        if filename is None:
            filename = f"jobs_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        filepath = f"data/{filename}"
        
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query("SELECT * FROM jobs ORDER BY scraped_at DESC", conn)
        
        if not df.empty and 'scraped_at' in df.columns:
            df['scraped_at'] = pd.to_datetime(df['scraped_at'])
        
        df.to_csv(filepath, index=False)
        logger.info(f"Exported {len(df)} jobs to {filepath}")
        return filepath

    # ======================
    # Description backfilling
    # ======================
    def get_jobs_missing_descriptions(self, limit: int = 100, exclude_failed: bool = True) -> pd.DataFrame:
        """Return jobs that have empty or NULL description, newest first.
        If exclude_failed=True, skip jobs marked as failed (description = 'FETCH_FAILED')."""
        with sqlite3.connect(self.db_path) as conn:
            if exclude_failed:
                df = pd.read_sql_query(
                    """
                    SELECT job_id, url, source, title, company, scraped_at
                    FROM jobs
                    WHERE (description IS NULL OR TRIM(description) = '')
                    AND description != 'FETCH_FAILED'
                    ORDER BY datetime(COALESCE(scraped_at, created_at)) DESC
                    LIMIT ?
                    """,
                    conn,
                    params=[limit]
                )
            else:
                df = pd.read_sql_query(
                    """
                    SELECT job_id, url, source, title, company, scraped_at
                    FROM jobs
                    WHERE description IS NULL OR TRIM(description) = ''
                    ORDER BY datetime(COALESCE(scraped_at, created_at)) DESC
                    LIMIT ?
                    """,
                    conn,
                    params=[limit]
                )
        return df

    def reset_failed_descriptions(self) -> int:
        """Reset jobs marked as FETCH_FAILED back to empty string to allow retrying."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            try:
                cur.execute("UPDATE jobs SET description = '' WHERE description = 'FETCH_FAILED'")
                conn.commit()
                count = cur.rowcount or 0
                if count > 0:
                    logger.info(f"Reset {count} jobs marked as failed back to empty for retrying")
                return count
            except Exception as e:
                logger.error(f"Failed resetting failed descriptions: {e}")
                return 0

    def get_failed_description_count(self) -> int:
        """Get count of jobs marked as FETCH_FAILED."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM jobs WHERE description = 'FETCH_FAILED'")
            return cur.fetchone()[0] or 0

    def mark_description_fetch_failed(self, job_ids: List[str]) -> int:
        """Mark jobs as having failed description fetch to avoid retrying them."""
        if not job_ids:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            try:
                cur.executemany(
                    "UPDATE jobs SET description = 'FETCH_FAILED' WHERE job_id = ?",
                    [(job_id,) for job_id in job_ids]
                )
                conn.commit()
                return cur.rowcount or 0
            except Exception as e:
                logger.error(f"Failed marking descriptions as failed: {e}")
                return 0

    def update_job_descriptions(self, updates: List[Dict[str, str]]) -> int:
        """Batch update job descriptions. Each item: {'job_id': ..., 'description': ...}."""
        if not updates:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            try:
                cur.executemany(
                    "UPDATE jobs SET description = ? WHERE job_id = ?",
                    [(u['description'], u['job_id']) for u in updates]
                )
                conn.commit()
                return cur.rowcount or 0
            except Exception as e:
                logger.error(f"Failed updating descriptions: {e}")
                return 0

    def _ensure_job_ids(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure DataFrame contains a vectorized job_id column"""
        if df.empty:
            df['job_id'] = pd.Series(dtype='string')
            return df
        df_clean = df.copy()
        for col in ['url', 'title', 'company', 'location', 'source']:
            if col in df_clean.columns:
                df_clean[col] = df_clean[col].astype(str).str.strip().str.lower()
            else:
                df_clean[col] = ''
        url_available = df_clean['url'].str.len() > 0
        fallback_id = (df_clean['title'] + '|' + df_clean['company'] + '|' + df_clean['location'] + '|' + df_clean['source'])
        job_ids = np.where(url_available, df_clean['url'], fallback_id)
        df['job_id'] = pd.Series(job_ids, index=df.index)
        return df

    # ======================
    # Parsed descriptions helpers
    # ======================
    def get_parsed_count(self, version: Optional[int] = None) -> int:
        """Return count of parsed description records, optionally filtered by version."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='parsed_descriptions'")
                exists = int(cur.fetchone()[0]) > 0
                if not exists:
                    return 0
                if version is None:
                    cur = conn.execute("SELECT COUNT(*) FROM parsed_descriptions")
                    return int(cur.fetchone()[0])
                cur = conn.execute("SELECT COUNT(*) FROM parsed_descriptions WHERE version = ?", (version,))
                return int(cur.fetchone()[0])
        except Exception:
            return 0
