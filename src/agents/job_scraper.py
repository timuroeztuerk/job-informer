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

    def _update_progress(self, completed: int, total: int, prefix: str = "") -> None:
        """Render a simple single-line progress bar in the terminal."""
        try:
            width = 30
            if total <= 0:
                bar = '-' * width
                line = f"\r{prefix}[{bar}] {completed}/{total}"
            else:
                filled = int(width * max(0, min(completed, total)) / total)
                bar = '█' * filled + '-' * (width - filled)
                line = f"\r{prefix}[{bar}] {completed}/{total}"
            sys.stdout.write(line)
            sys.stdout.flush()
            if total > 0 and completed >= total:
                sys.stdout.write("\n")
                sys.stdout.flush()
        except Exception:
            # Fallback silently if stdout is not available
            pass

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
        self.last_run_threshold_hit = False
        # Initialize database for fast deduplication
        self.db = JobDatabase()
        # Defaults
        self.request_timeout = getattr(self.config, 'request_timeout', 20)
        self._wait_tick_seconds = 1.0
        self._last_progress_line = ""
        self.max_total_jobs = max(0, int(getattr(self.config, 'max_total_jobs', 0)))
        self.min_new_jobs_to_continue = max(0, int(getattr(self.config, 'min_new_jobs_to_continue', 1)))

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
        stop_collecting = False

        for keyword in keywords:
            if stop_collecting:
                break
            for location in locations:
                # Add delay between requests
                time.sleep(self.config.request_delay)

                if self.config.enable_linkedin:
                    linkedin_jobs = self.scrape_linkedin(keyword, location)
                    if limit_per_source is not None and limit_per_source > 0:
                        linkedin_jobs = linkedin_jobs[:limit_per_source]

                    if self.max_total_jobs:
                        remaining_slots = self.max_total_jobs - len(all_jobs)
                        if remaining_slots <= 0:
                            stop_collecting = True
                            break
                        if len(linkedin_jobs) > remaining_slots:
                            linkedin_jobs = linkedin_jobs[:remaining_slots]
                            stop_collecting = True
                    all_jobs.extend(linkedin_jobs)
                    if self.max_total_jobs and len(all_jobs) >= self.max_total_jobs:
                        stop_collecting = True
                        logger.debug(
                            "Reached configured MAX_TOTAL_JOBS limit (%d); stopping further scraping",
                            self.max_total_jobs,
                        )
                        break
            if stop_collecting:
                break

        # Convert to DataFrame and process
        df = pd.DataFrame(all_jobs)
        if not df.empty:
            if self.max_total_jobs:
                df = df.head(self.max_total_jobs)
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

    def execute_job_search(self) -> bool:
        """Execute job search and send notifications"""
        from ..agents.email_sender import EmailSender

        email_sender = EmailSender(self.config)
        self.last_run_threshold_hit = False

        try:
            # Run automatic purge before scraping to clean up unwanted jobs (if enabled)
            if getattr(self.config, 'auto_purge_before_scraping', True):
                logger.info("Running automatic database cleanup before scraping...")
                try:
                    purge_success = self.purge_unwanted_jobs()
                    if purge_success:
                        logger.info("Database cleanup completed successfully")
                    else:
                        logger.warning("Database cleanup encountered issues but continuing with search")
                except Exception as e:
                    logger.warning(f"Database cleanup failed: {e}, continuing with search")
            else:
                logger.info("Automatic database cleanup disabled, skipping")

            # Parse keywords and locations from config
            keywords = [k.strip() for k in self.config.search_keywords.split(',')]
            locations = [l.strip() for l in self.config.search_locations.split(',')]
            jobs_df = self.scrape(keywords, locations)

            if not jobs_df.empty:
                # Apply additional filtering to newly scraped jobs
                jobs_df = self.filter_scraped_jobs(jobs_df)
                if self.max_total_jobs:
                    jobs_df = jobs_df.head(self.max_total_jobs)

                if jobs_df.empty:
                    logger.warning("All scraped jobs were filtered out by purge criteria")
                    # Send notification about filtered results (respect dry run)
                    subject = "Job Search Report - No jobs after filtering"
                    if not self.config.dry_run:
                        email_sender.send_job_report(jobs_df, subject)
                    else:
                        logger.info("DRY_RUN is enabled; skipping filtered-results email")
                    return False

                # Store jobs in SQLite database (fast deduplication)
                new_jobs_count = self.db.put_into_sql(jobs_df)

                if self.min_new_jobs_to_continue and new_jobs_count < self.min_new_jobs_to_continue:
                    self.last_run_threshold_hit = True
                    logger.info(
                        "Found %d new jobs, below MIN_NEW_JOBS_TO_CONTINUE=%d; skipping notifications",
                        new_jobs_count,
                        self.min_new_jobs_to_continue,
                    )
                    return False

                # Save CSV for backup/auditing unless dry-run
                csv_filename = None
                if not self.config.dry_run:
                    csv_filename = self.save_jobs_to_csv(jobs_df)
                else:
                    logger.info("DRY_RUN is enabled; skipping CSV save")
                
                # Send email with inline list of jobs (HTML + text), no attachment
                subject = f"Job Search Report - {new_jobs_count} new opportunities found!"
                if not self.config.dry_run and new_jobs_count > 0:
                    sent = email_sender.send_job_report(jobs_df, subject)
                    if not sent:
                        logger.warning("Email send returned False")
                elif self.config.dry_run:
                    logger.info("DRY_RUN is enabled; skipping email send")
                else:
                    logger.info("No new jobs detected; skipping email send")
                return True
            else:
                logger.warning("No jobs found in this search.")
                
                # Send notification about empty results (respect dry run)
                subject = "Job Search Report - No new opportunities found"
                if not self.config.dry_run:
                    email_sender.send_job_report(jobs_df, subject)
                else:
                    logger.info("DRY_RUN is enabled; skipping empty-results email")
                return False
                
        except Exception as e:
            logger.error(f"Error in job search: {e}")
            # Send error notification
            email_sender.send_error_notification(str(e))
            return False

    def get_search_summary(self) -> dict:
        """Get summary of search configuration"""
        return {
            'keywords': self.config.get_keywords_list(),
            'locations': self.config.get_locations_list(),
            'time_range': getattr(self.config, 'search_time_range', 'day'),
            'email_configured': bool(self.config.email_address and self.config.email_password),
            'recipient': self.config.recipient_email,
            'user_agent': self.config.user_agent[:50] + "..." if len(self.config.user_agent) > 50 else self.config.user_agent,
            'request_delay': self.config.request_delay,
            'max_retries': self.config.max_retries,
            'enable_linkedin': self.config.enable_linkedin,
            'dry_run': self.config.dry_run
        }

    def filter_scraped_jobs(self, jobs_df: pd.DataFrame) -> pd.DataFrame:
        """Apply purge filters to newly scraped jobs before storing/emailing them"""
        if jobs_df.empty:
            return jobs_df
            
        original_count = len(jobs_df)
        logger.info(f"Filtering {original_count} newly scraped jobs...")
        
        try:
            import re as _re
            
            # 1) Remove jobs matching unwanted keywords in TITLE
            unwanted_kw = self.config.get_unwanted_keywords_list()
            if unwanted_kw and 'title' in jobs_df.columns:
                escaped_kw = [_re.escape(k) for k in unwanted_kw if k]
                if escaped_kw:
                    patt_kw = "|".join(escaped_kw)
                    mask_keep = ~jobs_df['title'].astype(str).str.contains(patt_kw, case=False, na=False, regex=True)
                    jobs_df = jobs_df[mask_keep]
                    
            # 2) Remove jobs matching unwanted companies
            unwanted_companies = self.config.get_unwanted_companies_list()
            if unwanted_companies and 'company' in jobs_df.columns:
                terms = [_re.escape(c) for c in unwanted_companies if c]
                if terms:
                    patt_co = "|".join(terms)
                    mask_keep = ~jobs_df['company'].astype(str).str.contains(patt_co, case=False, na=False, regex=True)
                    jobs_df = jobs_df[mask_keep]
            
            # 3) Remove jobs with obvious part-time/internship indicators in title
            if 'title' in jobs_df.columns:
                employment_indicators = [
                    'part-time', 'part time', 'teilzeit',  # part-time variations
                    'intern', 'internship', 'praktik',     # internship variations
                    'contract', 'contractor', 'freiberufl', # contract variations
                    'werkstudent', 'working student',       # student positions
                    'trainee', 'ausbildung'                # trainee/apprenticeship
                ]
                # Use word boundary for more precise matching with non-capturing group
                employment_pattern = r'\b(?:' + '|'.join(_re.escape(term) for term in employment_indicators) + r')\b'
                mask_keep = ~jobs_df['title'].astype(str).str.contains(employment_pattern, case=False, na=False, regex=True)
                before_employment_filter = len(jobs_df)
                jobs_df = jobs_df[mask_keep]
                employment_filtered = before_employment_filter - len(jobs_df)
                if employment_filtered > 0:
                    logger.info(f"Filtered out {employment_filtered} jobs with unwanted employment indicators in title")
            
            filtered_count = len(jobs_df)
            removed_count = original_count - filtered_count
            
            if removed_count > 0:
                logger.info(f"Total filtered out: {removed_count} newly scraped jobs. Remaining: {filtered_count}")
            else:
                logger.info(f"All {original_count} newly scraped jobs passed all filters")
                
            return jobs_df
            
        except Exception as e:
            logger.error(f"Error filtering scraped jobs: {e}, returning original dataset")
            return jobs_df

    def purge_unwanted_jobs(self) -> bool:
        """Purge unwanted jobs from the database based on current filters"""
        try:
            # Get all jobs from database
            with self.db._get_connection() as conn:
                all_jobs_df = pd.read_sql_query("SELECT * FROM jobs ORDER BY scraped_at DESC", conn)
            
            if all_jobs_df.empty:
                logger.warning("No jobs found in database to purge")
                return True
            
            original_count = len(all_jobs_df)
            logger.info(f"Found {original_count} jobs in database")

            # 1) Remove jobs matching unwanted keywords in TITLE only (substring, case-insensitive)
            import re as _re
            unwanted_kw = self.config.get_unwanted_keywords_list()
            if unwanted_kw and 'title' in all_jobs_df.columns:
                # Escape each keyword for regex, but do NOT use word boundaries so partials match (e.g., 'game' -> 'Gameplay')
                escaped_kw = [_re.escape(k) for k in unwanted_kw if k]
                if escaped_kw:
                    patt_kw = "|".join(escaped_kw)
                    mask_title_unwanted = all_jobs_df['title'].astype(str).str.contains(patt_kw, case=False, na=False, regex=True)
                    unwanted_by_title = all_jobs_df[mask_title_unwanted]
                else:
                    unwanted_by_title = all_jobs_df.iloc[0:0]
            else:
                unwanted_by_title = all_jobs_df.iloc[0:0]

            # Unwanted by company names (COMPANY only, case-insensitive substring)
            unwanted_companies = self.config.get_unwanted_companies_list()
            if unwanted_companies and 'company' in all_jobs_df.columns:
                terms = [_re.escape(c) for c in unwanted_companies if c]
                if terms:
                    patt_co = "|".join(terms)
                    mask_company_unwanted = all_jobs_df['company'].astype(str).str.contains(patt_co, case=False, na=False, regex=True)
                    unwanted_by_company = all_jobs_df[mask_company_unwanted]
                else:
                    unwanted_by_company = all_jobs_df.iloc[0:0]
            else:
                unwanted_by_company = all_jobs_df.iloc[0:0]

            # 1.5) Remove jobs with part-time employment type from parsed descriptions
            import json
            part_time_job_ids = set()
            try:
                with self.db._get_connection() as conn:
                    part_time_df = pd.read_sql_query("""
                        SELECT DISTINCT j.job_id, j.title, j.company, p.payload_json
                        FROM jobs j
                        INNER JOIN parsed_descriptions p ON j.job_id = p.job_id
                        INNER JOIN (
                            SELECT job_id, MAX(version) as max_version
                            FROM parsed_descriptions
                            GROUP BY job_id
                        ) latest ON p.job_id = latest.job_id AND p.version = latest.max_version
                    """, conn)
                
                part_time_examples = []
                for _, row in part_time_df.iterrows():
                    try:
                        payload = json.loads(row['payload_json'])
                        employment_type = payload.get('employment_type', '').lower()
                        
                        # Concise combined check for part-time/contract/internship employment types
                        tokens = ('part-time', 'contract', 'internship')
                        if any(t in employment_type for t in tokens):
                            part_time_job_ids.add(row['job_id'])
                            if len(part_time_examples) < 3:
                                part_time_examples.append((row['title'], row['company']))

                    except (json.JSONDecodeError, KeyError):
                        continue
                
                if part_time_job_ids:
                    logger.info(f"Found {len(part_time_job_ids)} unwanted employment type jobs to remove based on parsed descriptions (part-time/contract/internship)")
                    if part_time_examples:
                        logger.info("Examples of unwanted employment type jobs:")
                        for title, company in part_time_examples:
                            logger.info(f"  - {title} at {company}")
                            
            except Exception as e:
                logger.warning(f"Could not check part-time jobs from parsed descriptions: {e}")

            # Combine unwanted ids
            unwanted_ids = set()
            if not unwanted_by_title.empty:
                unwanted_ids.update(unwanted_by_title['job_id'].tolist())
            if not unwanted_by_company.empty:
                unwanted_ids.update(unwanted_by_company['job_id'].tolist())
            if part_time_job_ids:
                unwanted_ids.update(part_time_job_ids)

            # 2) Remove duplicates: keep most recent per normalized (title, company, source)
            #    Note: We intentionally ignore location so entries with the same title+company
            #    but different locations are considered duplicates and purged.
            norm_df = all_jobs_df.copy()
            for col in ['title', 'company', 'location', 'source']:
                if col in norm_df.columns:
                    norm_df[f'{col}_norm'] = norm_df[col].astype(str).str.strip().str.lower()
                else:
                    norm_df[f'{col}_norm'] = ''
            # Parse scraped_at for recency sort
            if 'scraped_at' in norm_df.columns:
                try:
                    norm_df['scraped_at'] = pd.to_datetime(norm_df['scraped_at'], errors='coerce')
                except Exception:
                    pass
            norm_df['_order'] = norm_df['scraped_at']
            try:
                norm_df['_order'] = norm_df['_order'].fillna(pd.Timestamp(0))
            except Exception:
                pass

            norm_df_sorted = norm_df.sort_values(by=['_order'], ascending=False)
            keep_idx = norm_df_sorted.drop_duplicates(
                subset=['title_norm', 'company_norm', 'source_norm'], keep='first'
            ).index
            dup_mask = ~norm_df.index.isin(keep_idx)
            duplicate_jobs = all_jobs_df[dup_mask]
            duplicate_ids = set(duplicate_jobs['job_id'].tolist())

            # Union of all job_ids to delete
            to_delete_ids = list(unwanted_ids.union(duplicate_ids))

            if not to_delete_ids:
                logger.info("No unwanted or duplicate jobs found - database is clean")
                return True

            logger.info(
                f"Will remove {len(to_delete_ids)} jobs (unwanted: {len(unwanted_ids)} [keywords: {len(unwanted_by_title)}, companies: {len(unwanted_by_company)}, employment types: {len(part_time_job_ids)}], duplicates: {len(duplicate_ids)})"
            )

            # Delete selected jobs
            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.executemany("DELETE FROM jobs WHERE job_id = ?", [(jid,) for jid in to_delete_ids])
                    conn.commit()
                    
                    # Also clean up orphaned parsed descriptions for deleted jobs
                    if to_delete_ids:
                        cursor.executemany("DELETE FROM parsed_descriptions WHERE job_id = ?", [(jid,) for jid in to_delete_ids])
                        conn.commit()
                except Exception as e:
                    logger.error(f"Error during deletion: {e}")
                    return False

                # Verify deletion
                cursor.execute("SELECT COUNT(*) FROM jobs")
                final_count = cursor.fetchone()[0]

            logger.info(
                f"Purge complete: {len(to_delete_ids)} jobs removed (unwanted: {len(unwanted_ids)} [keywords: {len(unwanted_by_title)}, companies: {len(unwanted_by_company)}, employment types: {len(part_time_job_ids)}], duplicates: {len(duplicate_ids)}). Remaining: {final_count}"
            )

            if len(to_delete_ids) > 0:
                # Show examples
                frames = []
                if 'unwanted_by_title' in locals() and not unwanted_by_title.empty:
                    frames.append(unwanted_by_title)
                if 'unwanted_by_company' in locals() and not unwanted_by_company.empty:
                    frames.append(unwanted_by_company)
                frames.append(duplicate_jobs)
                sample_display = pd.concat(frames, ignore_index=True).head(3)
                if not sample_display.empty:
                    logger.info("Examples of removed jobs:")
                    for _, job in sample_display.iterrows():
                        logger.info(f"  - {job['title']} at {job['company']}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error purging unwanted jobs: {e}")
            return False