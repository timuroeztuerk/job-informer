"""
Job Scraper Module
Handles web scraping of job postings from various job sites
"""

from typing import List, Dict, Optional
import sys
import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
import time
import pandas as pd
from loguru import logger
from ..config.settings import Config
from ..utils.database import JobDatabase
from ..utils.data_utils import build_job_ids, build_normalized_keys, normalize_job_url
import os
import glob
from pathlib import Path
from urllib.parse import quote_plus
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
import random


class JobScraper:
    """Base class for job scraping functionality"""
    
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': config.user_agent})
        # Configure retries for robustness
        # Avoid automatic 429 retries to reduce hammering on rate limits
        retries = Retry(
            total=self.config.max_retries,
            backoff_factor=1.2,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        # Runtime state
        self.jobs_data = []
        # Initialize database for fast deduplication
        self.db = JobDatabase()
        # Defaults
        self.request_timeout = getattr(self.config, 'request_timeout', 20)
        self._wait_tick_seconds = 1.0
        self._last_progress_line = ""

    def _sleep_with_feedback(self, seconds: float, label: str = "waiting") -> None:
        """Sleep with periodic feedback logs so the user knows we're alive."""
        try:
            total = max(0.0, float(seconds))
        except Exception:
            total = 0.0
        if total <= 0:
            return
        tick = max(0.25, float(getattr(self, '_wait_tick_seconds', 1.0)))
        remaining = total
        logger.info(f"{label} — sleeping {total:.1f}s")
        while remaining > 0:
            step = min(tick, remaining)
            time.sleep(step)
            remaining -= step
            if remaining > 0:
                logger.info(f"{label} — {remaining:.1f}s remaining")

    def _progress_bar(self, current: int, total: int, width: int = 24) -> str:
        try:
            total = max(1, int(total))
            current = max(0, min(int(current), total))
            filled = int(width * current / total)
            return '█' * filled + '-' * (width - filled)
        except Exception:
            return '-' * width

    def _print_progress(self, prefix: str, current: int, total: int, suffix: str = "") -> None:
        try:
            bar = self._progress_bar(current, total)
            line = f"\r{prefix} [{bar}] {current}/{total} {suffix}".rstrip()
            sys.stdout.write(line)
            sys.stdout.flush()
            # store last line to restore after pauses
            try:
                self._last_progress_line = line
            except Exception:
                pass
            if current >= total:
                sys.stdout.write("\n")
                sys.stdout.flush()
        except Exception:
            pass

    def _sleep_quiet(self, seconds: float, prefix: str = "pause") -> None:
        try:
            total = max(0.0, float(seconds))
        except Exception:
            total = 0.0
        if total <= 0:
            return
        # minimal spinner
        spinner = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        tick = 0.2
        elapsed = 0.0
        while elapsed < total:
            ch = spinner[int((elapsed / tick)) % len(spinner)]
            remaining = max(0.0, total - elapsed)
            try:
                sys.stdout.write(f"\r{prefix} {ch} {remaining:4.1f}s")
                sys.stdout.flush()
            except Exception:
                pass
            time.sleep(min(tick, total - elapsed))
            elapsed += tick
        # At end, clear the spinner line and restore last progress (if any)
        try:
            sys.stdout.write("\r" + " " * 120 + "\r")
            if getattr(self, "_last_progress_line", ""):
                sys.stdout.write(self._last_progress_line)
            sys.stdout.flush()
        except Exception:
            pass

    def _get_unwanted_keywords(self) -> List[str]:
        """Return unwanted keywords list from configuration (cached per call site)."""
        try:
            return self.config.get_unwanted_keywords_list()
        except Exception:
            return []

    def _title_contains_unwanted_keywords(self, title: str) -> bool:
        """Fast check for unwanted keywords in a job title (case-insensitive, word boundaries)."""
        if not title:
            return False
        unwanted = self._get_unwanted_keywords()
        if not unwanted:
            return False
        import re
        escaped = [rf"\b{re.escape(k)}\b" for k in unwanted if k]
        if not escaped:
            return False
        pattern = "|".join(escaped)
        return re.search(pattern, title, re.IGNORECASE) is not None

    def _build_normalized_key_from_fields(self, source: str, title: str, company: str, url: str) -> str:
        """Build the same normalized key used for cross-run de-duplication without DataFrame overhead."""
        normalized_url = normalize_job_url(url or '', source or '')
        if normalized_url:
            return normalized_url
        # Fallback: source|title|company (lowercased, trimmed)
        safe = lambda s: (s or '').strip().lower()
        return f"{safe(source)}|{safe(title)}|{safe(company)}"

    def _get_existing_normalized_keys(self) -> set:
        """Fetch existing normalized keys directly from DB for faster membership checks."""
        try:
            with self.db._get_connection() as conn:
                df = pd.read_sql_query(
                    "SELECT normalized_key FROM jobs WHERE normalized_key IS NOT NULL",
                    conn
                )
            if df.empty:
                return set()
            return set(df['normalized_key'].astype(str).tolist())
        except Exception as e:
            logger.warning(f"Could not load existing normalized keys for pre-filtering: {e}")
            return set()

    # =======================
    # Description fetching API
    # =======================
    def _http_get_with_retries(self, url: str, timeout: Optional[int] = None) -> Optional[requests.Response]:
        """Lightweight GET with retries and basic LinkedIn-aware handling."""
        if timeout is None:
            timeout = self.request_timeout
        last_exc: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                headers = {
                    'User-Agent': self.config.user_agent,
                    'Accept-Language': random.choice(['en-US,en;q=0.9', 'en-GB,en;q=0.8', 'de-DE,de;q=0.8,en;q=0.7'])
                }
                resp = self.session.get(url, headers=headers, timeout=timeout)
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 429:
                    logger.info("HTTP 429 rate limit encountered while fetching description; backing off")
                    time.sleep(5 + random.uniform(0, 3))
                    return None
                if resp.status_code in (403, 999):
                    logger.warning(f"Blocked with HTTP {resp.status_code} for {url}")
                    return None
                logger.debug(f"HTTP {resp.status_code} on attempt {attempt+1} for {url}")
            except requests.RequestException as e:
                last_exc = e
                logger.debug(f"HTTP error on attempt {attempt+1} for {url}: {e}")
            time.sleep(min(5, 1.2 * (attempt + 1)))
        if last_exc:
            logger.debug(f"Giving up fetching {url}: {last_exc}")
        return None

    # =======================
    # Backfill-only helpers
    # =======================
    def _extract_linkedin_description(self, job_url: str) -> str:
        """Extract description from a LinkedIn job page. Used by backfill mode only."""
        if not job_url:
            return ''
        resp = self._http_get_with_retries(job_url, timeout=25)
        if not resp:
            return ''
        try:
            page = BeautifulSoup(resp.content, 'lxml')
            # 1) Newer layout
            desc = page.select_one('div.show-more-less-html__markup')
            if desc:
                return desc.get_text(separator=' ', strip=True)
            # 2) Legacy
            desc = page.select_one('div.description__text')
            if desc:
                return desc.get_text(separator=' ', strip=True)
            # 3) JSON-LD fallback
            ld = page.find('script', type='application/ld+json')
            ld_str = getattr(ld, 'string', None) if ld else None
            if ld_str:
                import json
                try:
                    data = json.loads(ld_str)
                    if isinstance(data, dict) and 'description' in data:
                        return BeautifulSoup(data['description'], 'html.parser').get_text(separator=' ', strip=True)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"LinkedIn description parse error: {e}")
        return ''

    def _extract_generic_description(self, job_url: str, source: str) -> str:
        """Best-effort description extraction for non-LinkedIn pages. Backfill-only."""
        if not job_url:
            return ''
        resp = self._http_get_with_retries(job_url, timeout=20)
        if not resp:
            return ''
        try:
            page = BeautifulSoup(resp.content, 'lxml')
            # Try a few common selectors
            selectors = [
                '#jobDescriptionText',
                'div#jobDescriptionText',
                'div.jobsearch-JobComponent-description',
                'section.jobsearch-jobDescriptionText',
                'div.job-description',
                'section.job-description',
            ]
            for sel in selectors:
                desc = page.select_one(sel)
                if desc:
                    return desc.get_text(separator=' ', strip=True)
        except Exception as e:
            logger.debug(f"Generic description parse error for {source}: {e}")
        return ''

    def fetch_job_description(self, url: str, source: str) -> str:
        """Fetch a textual description for a job URL. Used only by backfill mode."""
        src = (source or '').lower()
        if 'linkedin' in src or 'linkedin' in (url or '').lower():
            return self._extract_linkedin_description(url)
        return self._extract_generic_description(url, source)
    
    def scrape_linkedin(self, keywords: str, location: str) -> List[Dict]:
        """Scrape job postings from LinkedIn public listings with pagination and optional descriptions.
        - Applies: last 24 hours, full-time only
        - Paginates using the `start` parameter (25 results per page typical)
        - Optionally fetches job descriptions from detail pages (bounded concurrency)
        """
        logger.info(f"Scraping LinkedIn for '{keywords}' in '{location}' (last 24 hours, full-time)")

        def build_search_url(start: int) -> str:
            # f_TPR=r86400: last 24h, f_JT=F: Full-time
            # start: pagination offset (multiples of 25)
            # Keep URL minimal to reduce server-side quirks with paging.
            return (
                "https://www.linkedin.com/jobs/search/?"
                f"keywords={quote_plus(keywords)}"
                f"&location={quote_plus(location)}"
                f"&f_TPR=r86400&f_JT=F&start={start}&origin=JOB_SEARCH_PAGE_JOB_FILTER&trk=public_jobs_jobs-search-bar_search-submit"
            )

        def parse_cards(soup: BeautifulSoup) -> List[Dict]:
            cards = soup.find_all('div', class_='job-search-card')
            results: List[Dict] = []
            for card in cards:
                try:
                    if not isinstance(card, Tag):
                        continue
                    # Prefer stable selectors used by LinkedIn job cards (CSS-only to avoid class_ typing issues)
                    title_el = card.select_one('h3.base-search-card__title')
                    company_link_el = card.select_one('h4.base-search-card__subtitle a')
                    company_el = company_link_el or card.select_one('h4.base-search-card__subtitle')
                    location_el = card.select_one('span.job-search-card__location')
                    link_el = card.select_one('a.base-card__full-link') or card.select_one('a')
                    time_el = card.select_one('time')

                    job_url = link_el.get('href', '') if isinstance(link_el, Tag) else ''
                    job = {
                        'title': (title_el.get_text().strip() if isinstance(title_el, Tag) else ''),
                        'company': (company_el.get_text().strip() if isinstance(company_el, Tag) else ''),
                        'location': (location_el.get_text().strip() if isinstance(location_el, Tag) else location),
                        'source': 'LinkedIn',
                        'url': job_url,
                        'salary': 'Not specified',
                        'scraped_at': pd.Timestamp.now(),
                    }
                    # Posted date if available
                    if isinstance(time_el, Tag):
                        posted = time_el.get('datetime')
                        if posted:
                            job['posted_at'] = posted
                    results.append(job)
                except Exception as e:
                    logger.debug(f"LinkedIn card parse error: {e}")
                    continue
            return results

        hit_rate_limit = False
        rate_limit_logged = False

        def fetch_with_retries(url: str, timeout: Optional[int] = None) -> Optional[requests.Response]:
            if timeout is None:
                timeout = self.request_timeout
            last_exc = None
            for attempt in range(self.config.max_retries + 1):
                try:
                    # Vary Accept-Language and small jitter in headers to reduce blocks
                    headers = {
                        'User-Agent': self.config.user_agent,
                        'Accept-Language': random.choice(['en-US,en;q=0.9', 'en-GB,en;q=0.8', 'de-DE,de;q=0.8,en;q=0.7'])
                    }
                    resp = self.session.get(url, headers=headers, timeout=timeout)
                    if resp.status_code == 200:
                        return resp
                    if resp.status_code == 429:
                        nonlocal hit_rate_limit, rate_limit_logged
                        hit_rate_limit = True
                        if not rate_limit_logged:
                            logger.info(f"LinkedIn rate limited with HTTP 429; backing off and aborting {url}")
                            rate_limit_logged = True
                        else:
                            logger.debug(f"LinkedIn 429 on {url}; skipping.")
                        # Gentle backoff to reduce pressure
                        self._sleep_with_feedback(10 + random.uniform(0, 5), label="LinkedIn 429 backoff")
                        return None
                    # LinkedIn-specific anti-bot responses: stop early to avoid hammering
                    if resp.status_code in (403, 999):
                        logger.error(f"LinkedIn blocked the request with HTTP {resp.status_code}; aborting further retries for {url}")
                        return None
                    logger.warning(f"LinkedIn HTTP {resp.status_code} on attempt {attempt+1} for {url}")
                except requests.RequestException as e:
                    last_exc = e
                    logger.warning(f"LinkedIn request error on attempt {attempt+1}: {e}")
                self._sleep_with_feedback(min(5, 1.2 * (attempt + 1)), label="LinkedIn search retry backoff")
            if last_exc:
                logger.error(f"LinkedIn request failed after retries: {last_exc}")
            return None


        jobs: List[Dict] = []
        # Track normalized keys to dedup within this run
        seen_keys: set = set()
        # Load existing DB keys once to avoid fetching detail pages for known jobs
        existing_keys = self._get_existing_normalized_keys()
        skipped_existing = 0
        skipped_unwanted = 0
        max_pages = int(getattr(self.config, 'linkedin_max_pages', 4))
        page_size = 25

        try:
            no_progress_pages = 0
            page_index = 0  # Initialize page_index to ensure it is always defined
            for page_index in range(max_pages):
                start = page_index * page_size
                url = build_search_url(start)
                logger.debug(f"LinkedIn search URL (page {page_index+1}): {url}")
                resp = fetch_with_retries(url)
                if not resp:
                    break
                soup = BeautifulSoup(resp.content, 'lxml')
                page_jobs = parse_cards(soup)

                # Pre-filter and de-dup within the same run; also skip known DB jobs
                new_jobs: List[Dict] = []
                for j in page_jobs:
                    # Skip unwanted titles early
                    if self._title_contains_unwanted_keywords(j.get('title', '')):
                        skipped_unwanted += 1
                        continue

                    key = self._build_normalized_key_from_fields(
                        j.get('source', 'LinkedIn'), j.get('title', ''), j.get('company', ''), j.get('url', '')
                    )
                    if not key:
                        continue
                    if key in existing_keys or key in seen_keys:
                        skipped_existing += 1
                        continue
                    seen_keys.add(key)
                    new_jobs.append(j)
                jobs.extend(new_jobs)
                # compact progress update per page
                if not getattr(self.config, 'quiet_progress', True) or sys.stdout.isatty():
                    self._print_progress(
                        prefix=f"LinkedIn {keywords} @ {location}",
                        current=page_index + 1,
                        total=max_pages,
                        suffix=f"new:{len(jobs)} skipU:{skipped_unwanted} skipE:{skipped_existing}"
                    )

                # Stop conditions:
                # 1) No cards parsed → end reached or layout changed
                if not page_jobs:
                    logger.debug("No job cards found on this page; stopping pagination")
                    break
                # 2) No new unique jobs added for two consecutive pages → likely repeating results
                if len(new_jobs) == 0:
                    no_progress_pages += 1
                    if no_progress_pages >= 2:
                        logger.debug("No new jobs across two consecutive pages; stopping pagination")
                        break
                else:
                    no_progress_pages = 0

                # Polite delay with jitter (compact)
                if not getattr(self.config, 'quiet_progress', True) or sys.stdout.isatty():
                    self._sleep_quiet(self.config.request_delay + random.uniform(0.2, 0.8), prefix="pause")
                else:
                    time.sleep(self.config.request_delay + random.uniform(0.2, 0.8))

            # Default: do not fetch descriptions during scraping; set empty description field
            for j in jobs:
                j['description'] = ''

            # Final compact summary
            logger.info(
                f"LinkedIn summary — new:{len(jobs)} skipU:{skipped_unwanted} skipE:{skipped_existing} pages:{min(max_pages, page_index+1)}"
            )

        except Exception as e:
            logger.error(f"Error scraping LinkedIn: {e}")

        return jobs
    
    def get_most_recent_csv_file(self) -> Optional[str]:
        """Get the path to the most recent CSV file in the data directory"""
        data_dir = Path("data")
        if not data_dir.exists():
            return None
        
        # Find all CSV files with the pattern jobs_YYYYMMDD_HHMMSS.csv
        pattern = str(data_dir / "jobs_*.csv")
        csv_files = glob.glob(pattern)
        
        if not csv_files:
            return None
        
        # Sort files by modification time (newest first)
        csv_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        
        most_recent = csv_files[0]
        logger.info(f"Found most recent CSV file: {most_recent}")
        return most_recent
    
    def load_historical_jobs(self) -> pd.DataFrame:
        """Load jobs from the most recent CSV file"""
        recent_file = self.get_most_recent_csv_file()
        
        if recent_file is None:
            logger.info("No historical CSV files found")
            return pd.DataFrame()
        
        try:
            historical_df = pd.read_csv(recent_file)
            # Coerce scraped_at to datetime if present
            if 'scraped_at' in historical_df.columns:
                historical_df['scraped_at'] = pd.to_datetime(historical_df['scraped_at'], errors='coerce')
            logger.info(f"Loaded {len(historical_df)} historical jobs from {recent_file}")
            return historical_df
        except Exception as e:
            logger.error(f"Error loading historical CSV file {recent_file}: {e}")
            return pd.DataFrame()
    
    def remove_historical_duplicates(self, current_df: pd.DataFrame) -> pd.DataFrame:
        """Remove jobs that already exist in database using fast SQLite lookup"""
        if current_df.empty:
            return current_df
        
        original_count = len(current_df)
        
        # Build normalized keys for current data (canonical URL or source|title|company)
        current_keys = build_normalized_keys(current_df)

        # Build normalized keys for existing DB rows
        with self.db._get_connection() as conn:
            existing_df = pd.read_sql_query(
                "SELECT url, title, company, source FROM jobs",
                conn
            )
        if existing_df.empty:
            filtered_df = current_df
        else:
            existing_keys = set(build_normalized_keys(existing_df).tolist())
            # Keep only rows whose normalized key is not present in DB
            mask = ~current_keys.isin(existing_keys)
            filtered_df = current_df.loc[mask]
        
        filtered_count = len(filtered_df)
        removed_count = original_count - filtered_count
        
        if removed_count > 0:
            logger.info(f"Removed {removed_count} jobs that already exist in database")
        else:
            logger.info("No database duplicates found - all jobs are new!")
        
        return filtered_df
    
    def get_all_csv_files(self) -> List[str]:
        """Get all CSV files in the data directory sorted by date (newest first)"""
        data_dir = Path("data")
        if not data_dir.exists():
            return []
        
        # Find all CSV files with the pattern jobs_YYYYMMDD_HHMMSS.csv
        pattern = str(data_dir / "jobs_*.csv")
        csv_files = glob.glob(pattern)
        
        if not csv_files:
            return []
        
        # Sort files by modification time (newest first)
        csv_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        
        return csv_files
    
    def load_all_historical_jobs(self) -> pd.DataFrame:
        """Load and combine all historical job data"""
        csv_files = self.get_all_csv_files()
        
        if not csv_files:
            logger.info("No historical CSV files found")
            return pd.DataFrame()
        
        all_jobs = []
        total_files = len(csv_files)
        
        logger.info(f"Loading jobs from {total_files} CSV files...")
        
        for i, csv_file in enumerate(csv_files):
            try:
                df = pd.read_csv(csv_file)
                if 'scraped_at' in df.columns:
                    df['scraped_at'] = pd.to_datetime(df['scraped_at'], errors='coerce')
                all_jobs.append(df)
                logger.debug(f"Loaded {len(df)} jobs from {csv_file} ({i+1}/{total_files})")
            except Exception as e:
                logger.warning(f"Error loading {csv_file}: {e}")
                continue
        
        if not all_jobs:
            logger.warning("No valid CSV files could be loaded")
            return pd.DataFrame()
        
        # Combine all DataFrames
        combined_df = pd.concat(all_jobs, ignore_index=True)
        logger.info(f"Combined {len(combined_df)} total job records from all files")
        
        return combined_df
    
    def load_latest_historical_jobs(self, num_runs: int = 5) -> pd.DataFrame:
        """Load job data from the most recent N CSV files"""
        csv_files = self.get_all_csv_files()
        
        if not csv_files:
            logger.info("No historical CSV files found")
            return pd.DataFrame()
        
        # Take only the most recent N files
        recent_files = csv_files[:num_runs]
        actual_runs = len(recent_files)
        
        logger.info(f"Loading jobs from the most recent {actual_runs} CSV files...")
        
        recent_jobs = []
        
        for i, csv_file in enumerate(recent_files):
            try:
                df = pd.read_csv(csv_file)
                if 'scraped_at' in df.columns:
                    df['scraped_at'] = pd.to_datetime(df['scraped_at'], errors='coerce')
                recent_jobs.append(df)
                logger.debug(f"Loaded {len(df)} jobs from {csv_file} ({i+1}/{actual_runs})")
            except Exception as e:
                logger.warning(f"Error loading {csv_file}: {e}")
                continue
        
        if not recent_jobs:
            logger.warning("No valid recent CSV files could be loaded")
            return pd.DataFrame()
        
        # Combine recent DataFrames
        combined_df = pd.concat(recent_jobs, ignore_index=True)
        logger.info(f"Combined {len(combined_df)} job records from {actual_runs} recent files")
        
        return combined_df
    
    def filter_historical_jobs(self) -> pd.DataFrame:
        """Apply current filters to all historical job data and return filtered results"""
        logger.info("Filtering all historical job data...")
        
        # Load all historical jobs
        all_jobs_df = self.load_all_historical_jobs()
        
        if all_jobs_df.empty:
            logger.warning("No historical job data found to filter")
            return pd.DataFrame()
        
        original_count = len(all_jobs_df)
        logger.info(f"Loaded {original_count} total historical jobs")
        
        # Apply current filters
        filtered_df = self.filter_unwanted_jobs(all_jobs_df.copy())
        
        # Remove duplicates
        unique_filtered_df = filtered_df.drop_duplicates(subset=['title', 'company'], keep='first')
        
        final_count = len(unique_filtered_df)
        removed_count = original_count - final_count
        
        logger.info(f"After filtering: {final_count} jobs remaining ({removed_count} jobs removed)")
        
        return unique_filtered_df
    
    def scrape_all_sources(self, keywords: List[str], locations: List[str], *, limit_per_source: Optional[int] = None) -> pd.DataFrame:
        """Scrape jobs from enabled sources.
        - limit_per_source: if provided, cap collected jobs per source for speed (after pre-filtering).
        """
        all_jobs: List[Dict] = []

        if not self.config.enable_linkedin:
            logger.warning("No sources enabled. Enable ENABLE_LINKEDIN.")
            return pd.DataFrame()

        for keyword in keywords:
            for location in locations:
                # Add delay between requests
                time.sleep(self.config.request_delay)

                if self.config.enable_linkedin:
                    linkedin_jobs = self.scrape_linkedin(keyword, location)
                    if limit_per_source is not None and limit_per_source > 0:
                        linkedin_jobs = linkedin_jobs[:limit_per_source]
                    all_jobs.extend(linkedin_jobs)
                # Indeed removed
                # Porsche intentionally not called here to avoid duplicate results
        
        # Convert to DataFrame and process
        df = pd.DataFrame(all_jobs)
        if not df.empty:
            # Filter out jobs with unwanted keywords in title
            df = self.filter_unwanted_jobs(df)
            
            # Build job_ids vectorized and drop duplicates
            df['job_id'] = self._build_job_ids_vectorized(df)
            df = df.drop_duplicates(subset=['job_id'], keep='first')
            logger.info(f"Current run found {len(df)} unique jobs after filtering")
            
            # Remove jobs that were found in previous runs
            df = self.remove_historical_duplicates(df)
            
            logger.info(f"Final count after removing historical duplicates: {len(df)}")
        else:
            logger.warning("No jobs found")
            
        return df
    
    def filter_unwanted_jobs(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter out jobs with unwanted keywords in the title and unwanted companies"""
        if df.empty:
            return df
            
        original_count = len(df)
        
        # Get unwanted keywords from configuration
        unwanted_keywords = self.config.get_unwanted_keywords_list()
        unwanted_companies = []
        try:
            unwanted_companies = self.config.get_unwanted_companies_list()
        except Exception:
            unwanted_companies = []

        if unwanted_keywords:
            # Build a single regex pattern with word boundaries for all keywords
            import re
            escaped = [rf"\b{re.escape(k)}\b" for k in unwanted_keywords if k]
            if escaped:
                pattern = "|".join(escaped)
                mask = ~df['title'].str.contains(pattern, case=False, na=False, regex=True)
                df = df.loc[mask]

        if unwanted_companies and 'company' in df.columns:
            import re
            terms = [re.escape(c) for c in unwanted_companies if c]
            if terms:
                patt = "|".join(terms)
                mask = ~df['company'].astype(str).str.contains(patt, case=False, na=False, regex=True)
                df = df.loc[mask]
        
        filtered_count = len(df)
        removed_count = original_count - filtered_count
        
        if removed_count > 0:
            logger.info(f"Filtered out {removed_count} jobs by unwanted filters (keywords/companies)")
            
        return df

    def _build_job_ids_vectorized(self, df: pd.DataFrame) -> pd.Series:
        """Build stable identifiers using normalized URLs; fallback to source|title|company."""
        return build_job_ids(df)
    
    def save_jobs_to_csv(self, df: pd.DataFrame, filename: Optional[str] = None) -> str:
        """Save jobs data to CSV file"""
        if filename is None:
            filename = f"jobs_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(data_dir / filename)
        df.to_csv(filepath, index=False)
        logger.info(f"Jobs data saved to {filepath}")
        return filepath
