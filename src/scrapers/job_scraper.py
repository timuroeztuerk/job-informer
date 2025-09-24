"""
Job Scraper Module - Currently only LinkedIn, which my experience says is enough for now.
"""
import os
import glob
import time
import random
from typing import List, Dict, Optional, Tuple
import sys
import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
import pandas as pd
from loguru import logger
from pathlib import Path
from urllib.parse import quote_plus
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

from ..config.settings import Config, DEFAULT_TIME_RANGE
from ..utils.database import JobDatabase
from ..utils.data_utils import build_job_ids, build_normalized_keys, normalize_job_url, remove_historical_duplicates, filter_unwanted_jobs
from ..utils.terminal_utils import _sleep_with_feedback, _print_progress, _progress_bar, _sleep_quiet

TIME_RANGE_TO_SECONDS = {
    "day": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
    "month": 30 * 24 * 60 * 60,
}


class JobScraper:
    """Base class for job scraping functionality"""
    _sleep_with_feedback = _sleep_with_feedback
    _print_progress = _print_progress
    _progress_bar = _progress_bar
    _sleep_quiet = _sleep_quiet
    build_normalized_keys = build_normalized_keys
    remove_historical_duplicates = remove_historical_duplicates
    filter_unwanted_jobs = filter_unwanted_jobs

    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': config.user_agent})
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

    def purge_keywords(self, title: str) -> bool:
        """Fast check for unwanted keywords in a job title using substring matching (case-insensitive). Example: 'student' will match 'Werkstudent', 'steuer' will match 'Steuerfach'."""
        if not title:
            return False
        unwanted = self.config.get_unwanted_keywords_list()
        if not unwanted:
            return False
        title_l = title.lower()
        return any((k or '').lower() in title_l for k in unwanted if k)

    def purge_companies(self, company: str) -> bool:
        """Check if company name contains any unwanted company substring (case-insensitive)."""
        if not company:
            return False
        try:
            unwanted_companies = self.config.get_unwanted_companies_list()
        except Exception:
            return False
        if not unwanted_companies:
            return False
        c_l = company.lower()
        return any((c or '').lower() in c_l for c in unwanted_companies if c)

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

    def _get_linkedin_time_filter(self) -> Tuple[int, str]:
        """Resolve the configured time range to LinkedIn's `f_TPR` seconds filter."""
        configured = getattr(self.config, 'search_time_range', DEFAULT_TIME_RANGE)
        normalized = (configured or DEFAULT_TIME_RANGE).strip().lower()
        seconds = TIME_RANGE_TO_SECONDS.get(normalized)
        if seconds is None:
            logger.warning(
                "Unsupported time range '{}' provided; defaulting to '{}'",
                configured,
                DEFAULT_TIME_RANGE,
            )
            normalized = DEFAULT_TIME_RANGE
            seconds = TIME_RANGE_TO_SECONDS[DEFAULT_TIME_RANGE]
        return seconds, normalized

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

    def scrape_linkedin(self, keywords: str, location: str) -> List[Dict]:
        """Scrape job postings from LinkedIn public listings with pagination and optional descriptions.
        - Applies: configurable time range (day/week/month), full-time only
        - Paginates using the `start` parameter (25 results per page typical)
        - Optionally fetches job descriptions from detail pages (bounded concurrency)
        """
        time_filter_seconds, normalized_time_range = self._get_linkedin_time_filter()
        logger.debug(
            f"Scraping LinkedIn for '{keywords}' in '{location}' (time range: {normalized_time_range}, full-time)"
        )

        def build_search_url(start: int) -> str:
            return (
                "https://www.linkedin.com/jobs/search/?"
                f"keywords={quote_plus(keywords)}"
                f"&location={quote_plus(location)}"
                f"&f_TPR=r{time_filter_seconds}&f_JT=F&start={start}&origin=JOB_SEARCH_PAGE_JOB_FILTER&trk=public_jobs_jobs-search-bar_search-submit"
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
        seen_keys: set = set()
        existing_keys = self._get_existing_normalized_keys()
        skipped_existing = 0
        skipped_unwanted = 0

        try:
            start = 0
            url = build_search_url(start)
            resp = fetch_with_retries(url)
            if not resp:
                page_jobs: List[Dict] = []
            else:
                soup = BeautifulSoup(resp.content, 'lxml')
                page_jobs = parse_cards(soup)

            # Pre-filter and de-dup within the same run; also skip known DB jobs
            new_jobs: List[Dict] = []
            for j in page_jobs:
            # Skip unwanted titles early
                if self.purge_keywords(j.get('title', '')) or self.purge_companies(j.get('company', '')):
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

            # Progress for a single page
            if not getattr(self.config, 'quiet_progress', True) or sys.stdout.isatty():
                self._print_progress(
                    prefix=f"LinkedIn {keywords} @ {location}",
                    current=1,
                    total=1,
                    suffix=f"New:{len(jobs)} Unwanted:{skipped_unwanted} Existing:{skipped_existing}"
                )

            # Default: do not fetch descriptions during scraping; set empty description field
            for j in jobs:
                j['description'] = ''
        except Exception as e:
            logger.error(f"Error scraping LinkedIn: {e}")

        return jobs

    def scrape(self, keywords: List[str], locations: List[str], *, limit_per_source: Optional[int] = None) -> pd.DataFrame:
        """Scrape jobs from enabled sources.
        """
        all_jobs: List[Dict] = []

        for keyword in keywords:
            for location in locations:
                # Add delay between requests
                time.sleep(self.config.request_delay)

                if self.config.enable_linkedin:
                    linkedin_jobs = self.scrape_linkedin(keyword, location)
                    if limit_per_source is not None and limit_per_source > 0:
                        linkedin_jobs = linkedin_jobs[:limit_per_source]
                    all_jobs.extend(linkedin_jobs)
        
        # Convert to DataFrame and process
        df = pd.DataFrame(all_jobs)
        if not df.empty:
            # Filter out jobs with unwanted keywords in title
            df = self.filter_unwanted_jobs(df)
            # Build job_ids vectorized and drop duplicates
            df['job_id'] = build_job_ids(df)
            df = df.drop_duplicates(subset=['job_id'], keep='first')
            # Remove jobs that were found in previous runs
            df = self.remove_historical_duplicates(df)
            
        else:
            logger.warning("No jobs found")
            
        return df
    
    def save_jobs_to_csv(self, df: pd.DataFrame, filename: Optional[str] = None) -> str:
        """Save jobs data to CSV file"""
        if filename is None:
            filename = f"jobs_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv"
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(data_dir / filename)
        df.to_csv(filepath, index=False)
        return filepath