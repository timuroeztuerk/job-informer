"""
Job Scraper Module
Handles web scraping of job postings from various job sites
"""

from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
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
        retries = Retry(total=self.config.max_retries, backoff_factor=1.2,
                        status_forcelist=[500, 502, 503, 504],
                        allowed_methods=["GET", "HEAD"])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.jobs_data: List[Dict] = []
        # Initialize database for fast deduplication
        self.db = JobDatabase()
        # Reusable Selenium driver (lazy init)
        self._driver: Optional[webdriver.Chrome] = None
        # Default request timeout (seconds)
        self.request_timeout: int = getattr(self.config, 'request_timeout', 20)
        
        # Default wait feedback granularity (seconds)
        self._wait_tick_seconds: float = 1.0

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
        """Fetch existing jobs' normalized keys from the database for quick membership checks."""
        try:
            with self.db._get_connection() as conn:
                existing_df = pd.read_sql_query(
                    "SELECT url, title, company, source FROM jobs",
                    conn
                )
            if existing_df.empty:
                return set()
            return set(build_normalized_keys(existing_df).tolist())
        except Exception as e:
            logger.warning(f"Could not load existing jobs for pre-filtering: {e}")
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

    def _extract_linkedin_description(self, job_url: str) -> str:
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
        if not job_url:
            return ''
        resp = self._http_get_with_retries(job_url, timeout=20)
        if not resp:
            return ''
        try:
            page = BeautifulSoup(resp.content, 'lxml')
            # Try a few common selectors (Indeed and others)
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
        """Fetch a textual description for a job URL, dispatching by source."""
        src = (source or '').lower()
        if 'linkedin' in src or 'linkedin' in (url or '').lower():
            return self._extract_linkedin_description(url)
        return self._extract_generic_description(url, source)

    def get_driver(self) -> webdriver.Chrome:
        """Return a reusable Chrome WebDriver, creating it if needed."""
        try:
            if self._driver:
                # touch the session to ensure it's alive
                _ = self._driver.current_url
                return self._driver
        except Exception:
            try:
                driver = self._driver
                if driver is not None:
                    driver.quit()
            except Exception:
                pass
            self._driver = None
        self._driver = self.setup_driver()
        return self._driver

    def close_driver(self) -> None:
        """Close and cleanup the reusable driver."""
        driver = getattr(self, '_driver', None)
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
            self._driver = None

    def __del__(self):
        # Best-effort cleanup
        try:
            self.close_driver()
        except Exception:
            pass
        
    def setup_driver(self) -> webdriver.Chrome:
        """Create a new Chrome WebDriver with appropriate options (no reuse)."""
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument(f'--user-agent={self.config.user_agent}')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        try:
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined})'
            })
        except Exception:
            pass
        return driver
    
    def scrape_indeed(self, keywords: str, location: str) -> List[Dict]:
        """Scrape job postings from Indeed"""
        logger.info(f"Scraping Indeed for '{keywords}' in '{location}'")
        jobs = []
        skipped_existing = 0
        skipped_unwanted = 0
        existing_keys = self._get_existing_normalized_keys()
        seen_keys: set = set()
        try:
            driver = self.get_driver()

            # Build Indeed search URL
            base_url = "https://www.indeed.com/jobs"
            url = f"{base_url}?q={quote_plus(keywords)}&l={quote_plus(location)}&sort=date"
            driver.get(url)

            max_pages = int(getattr(self.config, 'indeed_max_pages', 2))
            current_page = 1

            while True:
                # Wait for job listings to load on the page
                WebDriverWait(driver, 20).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "[data-jk]"))
                )

                job_cards = driver.find_elements(By.CSS_SELECTOR, "[data-jk]")

                for card in job_cards[:20]:  # Limit to first 20 results
                    try:
                        title_element = card.find_element(By.CSS_SELECTOR, "h2 a span")
                        company_element = card.find_element(By.CSS_SELECTOR, "[data-testid='company-name']")
                        location_element = card.find_element(By.CSS_SELECTOR, "[data-testid='job-location']")

                        job_data = {
                            'title': title_element.text.strip(),
                            'company': company_element.text.strip(),
                            'location': location_element.text.strip(),
                            'source': 'Indeed',
                            'url': normalize_job_url(card.find_element(By.CSS_SELECTOR, "h2 a").get_attribute('href') or '', 'Indeed'),
                            'scraped_at': pd.Timestamp.now()
                        }

                        # Pre-filter: skip unwanted titles early
                        if self._title_contains_unwanted_keywords(job_data['title']):
                            skipped_unwanted += 1
                            continue

                        # Pre-filter: skip jobs already known in DB or within this run
                        key = self._build_normalized_key_from_fields(
                            job_data.get('source', ''), job_data.get('title', ''), job_data.get('company', ''), job_data.get('url', '')
                        )
                        if key in existing_keys or key in seen_keys:
                            skipped_existing += 1
                            continue
                        seen_keys.add(key)

                        # Try to get salary if available
                        try:
                            salary_element = card.find_element(By.CSS_SELECTOR, "[data-testid='salary-snippet']")
                            job_data['salary'] = salary_element.text.strip()
                        except:
                            job_data['salary'] = 'Not specified'

                        jobs.append(job_data)

                    except Exception as e:
                        logger.warning(f"Error extracting job data: {e}")
                        continue

                # Try to go to next page (bounded by max_pages)
                if current_page >= max_pages:
                    break
                try:
                    next_btn = driver.find_element(By.CSS_SELECTOR, "a[aria-label='Next'], a[aria-label='Next Page'], a[rel='next']")
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
                    self._sleep_with_feedback(0.3, label="Scrolling to next Indeed page")
                    next_btn.click()
                    current_page += 1
                    # polite delay with jitter
                    self._sleep_with_feedback(self.config.request_delay + random.uniform(0.2, 0.8), label="Between Indeed pages")
                except NoSuchElementException:
                    break
                except Exception:
                    break

            if skipped_unwanted or skipped_existing:
                logger.info(
                    f"Indeed pre-filtering skipped {skipped_unwanted} unwanted and {skipped_existing} existing/duplicate jobs"
                )
            logger.info(f"Scraped {len(jobs)} jobs from Indeed across {current_page} page(s)")

        except Exception as e:
            logger.error(f"Error scraping Indeed: {e}")

        return jobs

    def scrape_porsche(self) -> List[Dict]:
        """Scrape Porsche careers first page using Selenium-rendered DOM (handles JS-rendered results)."""
        jobs: List[Dict] = []
        url = getattr(self.config, 'porsche_search_url', '')
        if not url:
            logger.warning("PORSCHE_SEARCH_URL not configured; skipping Porsche scrape")
            return jobs
        try:
            logger.info("Scraping Porsche careers site")
            driver = self.get_driver()
            driver.get(url)

            # Try to accept cookie banner if present to reveal results
            try:
                # Common buttons: "Akzeptieren", "Accept", or role=button with consent id
                consent_btns = driver.find_elements(By.XPATH, "//button[contains(translate(., 'ACEPTKZIRN', 'aceptkzirn'), 'accept') or contains(., 'Akzeptieren') or contains(., 'Einverstanden')]")
                if consent_btns:
                    consent_btns[0].click()
            except Exception:
                pass

            # Wait for anchors to job detail pages to appear
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='index.php?ac=jobad']"))
                )
            except Exception:
                # Give it a brief extra chance
                self._sleep_with_feedback(2.0, label="Waiting for Porsche results")

            link_elements = driver.find_elements(By.CSS_SELECTOR, "a[href*='index.php?ac=jobad']")
            existing_keys = self._get_existing_normalized_keys()
            seen_keys: set = set()
            skipped_unwanted = 0
            skipped_existing = 0

            def add_job(title: str, location_text: str, link: str):
                nonlocal skipped_unwanted, skipped_existing
                job_url = normalize_job_url(link or '', 'Porsche')
                title = (title or '').strip()
                if self._title_contains_unwanted_keywords(title):
                    skipped_unwanted += 1
                    return
                key = self._build_normalized_key_from_fields('Porsche', title, 'Porsche', job_url)
                if key in existing_keys or key in seen_keys:
                    skipped_existing += 1
                    return
                seen_keys.add(key)
                jobs.append({
                    'title': title,
                    'company': 'Porsche',
                    'location': (location_text or '').strip() or 'Germany',
                    'source': 'Porsche',
                    'url': job_url,
                    'salary': 'Not specified',
                    'description': '',
                    'scraped_at': pd.Timestamp.now(),
                })

            for a in link_elements[:50]:
                try:
                    href = a.get_attribute('href') or ''
                    # Title: anchor text or nearest heading within same card
                    title_text = (a.text or '').strip()
                    if not title_text:
                        try:
                            title_el = a.find_element(By.XPATH, "./ancestor::*[self::li or self::div][1]//h2|./ancestor::*[self::li or self::div][1]//h3")
                            title_text = title_el.text.strip()
                        except Exception:
                            title_text = ''
                    # Location: try common classes near the anchor
                    location_text = ''
                    try:
                        container = a.find_element(By.XPATH, "./ancestor::*[self::li or self::div][1]")
                        try:
                            loc_el = container.find_element(By.CSS_SELECTOR, ".job-location, .location, .job-offer__location")
                            location_text = loc_el.text.strip()
                        except Exception:
                            location_text = ''
                    except Exception:
                        location_text = ''
                    if href:
                        add_job(title_text, location_text, href)
                except Exception:
                    continue

            if skipped_unwanted or skipped_existing:
                logger.info(f"Porsche pre-filtering skipped {skipped_unwanted} unwanted and {skipped_existing} existing/duplicate jobs")
            logger.info(f"Scraped {len(jobs)} jobs from Porsche")
        except Exception as e:
            logger.error(f"Error scraping Porsche: {e}")
        return jobs
    
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
                f"&f_TPR=r604800&f_JT=F&start={start}&origin=JOB_SEARCH_PAGE_JOB_FILTER&trk=public_jobs_jobs-search-bar_search-submit"
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

        def extract_description(job_url: str) -> str:
            if not job_url:
                return ''
            resp = fetch_with_retries(job_url, timeout=25)
            if not resp:
                return ''
            try:
                page = BeautifulSoup(resp.content, 'lxml')
                # Common containers for description (structure may change)
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
                logger.debug(f"Description parse error: {e}")
            return ''

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
                logger.info(f"LinkedIn page {page_index+1}: {len(new_jobs)} new jobs (total {len(jobs)})")

                # Stop conditions:
                # 1) No cards parsed → end reached or layout changed
                if not page_jobs:
                    logger.debug("No job cards found on this page; stopping pagination")
                    break
                # 2) No new unique jobs added for two consecutive pages → likely repeating results
                if len(new_jobs) == 0:
                    no_progress_pages += 1
                    if no_progress_pages >= 2:
                        logger.info("No new jobs across two consecutive pages; stopping pagination")
                        break
                else:
                    no_progress_pages = 0

                # Polite delay with jitter
            self._sleep_with_feedback(self.config.request_delay + random.uniform(0.2, 0.8), label="Between LinkedIn pages")

            # Optionally fetch job descriptions with a gentle sequential approach and early abort on 429
            MAX_DESC_FETCH = int(getattr(self.config, 'linkedin_desc_max', 8))
            targets = [j for j in jobs if j.get('url')][:MAX_DESC_FETCH]
            if targets and not hit_rate_limit:
                logger.info(f"Fetching LinkedIn job descriptions for up to {len(targets)} jobs (sequential)...")
                for j in targets:
                    if hit_rate_limit:
                        break
                    try:
                        desc = extract_description(j['url'])
                    except Exception:
                        desc = ''
                    j['description'] = desc or ''
                    if hit_rate_limit:
                        logger.info("Detected rate limiting while fetching descriptions; stopping early.")
                        break
                    # gentle pacing between detail requests
                    self._sleep_with_feedback(1.0 + random.uniform(0.3, 0.9), label="Between description requests")
            elif hit_rate_limit:
                logger.info("Skipping job description fetching due to detected rate limiting (429)")
            # Jobs beyond target range: default empty description
            for j in jobs:
                if 'description' not in j:
                    j['description'] = ''

            if skipped_unwanted or skipped_existing:
                logger.info(
                    f"LinkedIn pre-filtering skipped {skipped_unwanted} unwanted and {skipped_existing} existing/duplicate jobs"
                )
            logger.info(f"Scraped {len(jobs)} jobs from LinkedIn (last 24 hours, full-time)")

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
            filtered_df = current_df[mask]
        
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
    
    def scrape_all_sources(self, keywords: List[str], locations: List[str], *, limit_per_source: Optional[int] = None, skip_selenium: bool = False) -> pd.DataFrame:
        """Scrape jobs from enabled sources.
        - limit_per_source: if provided, cap collected jobs per source for speed (after pre-filtering).
        - skip_selenium: if true, skip Selenium-based sources like Porsche or Indeed dynamic flows.
        """
        all_jobs: List[Dict] = []

        if not (self.config.enable_linkedin or self.config.enable_indeed or getattr(self.config, 'enable_porsche', False)):
            logger.warning("No sources enabled. Enable at least one of ENABLE_LINKEDIN or ENABLE_INDEED.")
            return pd.DataFrame()

        # Porsche: one-shot scrape using configured URL
        if getattr(self.config, 'enable_porsche', False) and not skip_selenium:
            logger.info("Starting Porsche one-shot scrape (no keyword/location iteration)")
            try:
                porsche_jobs = self.scrape_porsche()
                if limit_per_source is not None and limit_per_source > 0:
                    porsche_jobs = porsche_jobs[:limit_per_source]
                all_jobs.extend(porsche_jobs)
            except Exception as e:
                logger.warning(f"Porsche scrape skipped due to error: {e}")
        elif getattr(self.config, 'enable_porsche', False) and skip_selenium:
            logger.info("Skipping Porsche due to skip_selenium=true")

        for keyword in keywords:
            for location in locations:
                # Add delay between requests
                time.sleep(self.config.request_delay)

                if self.config.enable_linkedin:
                    linkedin_jobs = self.scrape_linkedin(keyword, location)
                    if limit_per_source is not None and limit_per_source > 0:
                        linkedin_jobs = linkedin_jobs[:limit_per_source]
                    all_jobs.extend(linkedin_jobs)
                if self.config.enable_indeed and not skip_selenium:
                    indeed_jobs = self.scrape_indeed(keyword, location)
                    if limit_per_source is not None and limit_per_source > 0:
                        indeed_jobs = indeed_jobs[:limit_per_source]
                    all_jobs.extend(indeed_jobs)
                elif self.config.enable_indeed and skip_selenium:
                    logger.info("Skipping Indeed due to skip_selenium=true")
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
        """Filter out jobs with unwanted keywords in the title"""
        if df.empty:
            return df
            
        original_count = len(df)
        
        # Get unwanted keywords from configuration
        unwanted_keywords = self.config.get_unwanted_keywords_list()

        if unwanted_keywords:
            # Build a single regex pattern with word boundaries for all keywords
            import re
            escaped = [rf"\b{re.escape(k)}\b" for k in unwanted_keywords if k]
            if escaped:
                pattern = "|".join(escaped)
                df = df[~df['title'].str.contains(pattern, case=False, na=False, regex=True)]
        
        filtered_count = len(df)
        removed_count = original_count - filtered_count
        
        if removed_count > 0:
            logger.info(f"Filtered out {removed_count} jobs containing unwanted keywords")
            
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
