"""
Job Scraper Module - Currently only LinkedIn, which my experience says is enough for now.
"""
import time
import random
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
import pandas as pd
from loguru import logger
from urllib.parse import quote_plus, urljoin
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

from ..config.settings import Config, DEFAULT_TIME_RANGE
from ..utils.database import JobDatabase
from ..utils.data_utils import build_job_ids, build_normalized_key, normalize_job_url
from ..utils.query_groups import QuerySpec, query_specs_for_terms
from ..utils.relevance import (
    RULESET_VERSION,
    classify_relevance,
    decision_for_result,
)
from ..utils.time_utils import utc_now, utc_now_iso
from ..utils.run_progress import update_api_run_progress
from ..utils.filtering import (
    match_academic_title_pattern,
    match_company_filter,
    match_keyword_filter,
    match_study_title_pattern,
)

TIME_RANGE_TO_SECONDS = {
    "day": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
    "month": 30 * 24 * 60 * 60,
}



def _collection_progress_metrics(
    observed: int,
    *,
    new: int = 0,
    archived: int = 0,
) -> dict[str, int]:
    return {
        "observed": observed,
        "new": new,
        "archived": archived,
    }


@dataclass
class LinkedInRequestStats:
    consecutive_failures: int = 0
    blocked_until_ts: float = 0.0
    last_status: Optional[int] = None
    last_error: Optional[str] = None
    requests_attempted: int = 0
    request_failures: int = 0
    rate_limit_responses: int = 0


class LinkedInSource:
    """Collect and parse LinkedIn's public job-search result pages."""

    name = "linkedin"
    display_name = "LinkedIn"
    base_backoff_seconds = 4.0
    max_backoff_seconds = 240.0

    def __init__(self, session: requests.Session, config: Config):
        self.session = session
        self.config = config
        self.stats = LinkedInRequestStats()
        self.last_query_report: Dict[str, object] = {}
        self.request_timeout = int(getattr(config, "request_timeout", 20))
        self.max_retries = max(1, int(getattr(config, "max_retries", 2)))

    @property
    def is_in_cooldown(self) -> bool:
        return time.time() < self.stats.blocked_until_ts

    @property
    def cooldown_remaining(self) -> float:
        remaining = self.stats.blocked_until_ts - time.time()
        return max(0.0, remaining)

    def _build_request_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.config.user_agent,
            "Accept-Language": random.choice(
                ["en-US,en;q=0.9", "en-GB,en;q=0.8", "de-DE,de;q=0.8,en;q=0.7"]
            ),
        }

    def mark_success(self) -> None:
        self.stats.consecutive_failures = 0
        self.stats.last_status = 200
        self.stats.last_error = None

    def _failure_backoff_seconds(self, status_code: Optional[int] = None) -> float:
        status_boost = 1.0
        if status_code in (403, 429, 999):
            status_boost = 2.8
        elif status_code and 500 <= status_code < 600:
            status_boost = 1.8
        exponent = min(self.stats.consecutive_failures, 5)
        base = self.base_backoff_seconds * (2 ** exponent) * status_boost
        jitter = random.uniform(0.75, 1.5)
        return min(self.max_backoff_seconds, base) * jitter

    def _retry_sleep_seconds(self, attempt: int) -> float:
        return min(8.0, 0.6 * (2 ** attempt)) + random.uniform(0, 1.0)

    def mark_http_failure(self, status_code: Optional[int] = None, message: Optional[str] = None) -> None:
        self.stats.consecutive_failures += 1
        self.stats.last_status = status_code
        self.stats.last_error = message
        cooldown_seconds = self._failure_backoff_seconds(status_code)
        if status_code in (403, 429, 999):
            logger.warning(
                "{} blocked (status={}); entering source cooldown for {:.0f}s.",
                self.display_name,
                status_code,
                cooldown_seconds,
            )
        self.stats.blocked_until_ts = time.time() + cooldown_seconds

    def mark_network_failure(self, exc: Exception) -> None:
        self.stats.consecutive_failures += 1
        self.stats.request_failures += 1
        self.stats.last_status = None
        self.stats.last_error = str(exc)
        self.stats.blocked_until_ts = time.time() + self._failure_backoff_seconds(None)

    def _wait_for_cooldown(self) -> None:
        remaining = self.cooldown_remaining
        if remaining <= 0:
            return
        logger.info(
            "{} is rate-limited; waiting {:.1f}s before retrying the same request.",
            self.display_name,
            remaining,
        )
        time.sleep(remaining)

    def request(self, url: str, timeout: Optional[int] = None) -> Optional[requests.Response]:
        if self.is_in_cooldown:
            self._wait_for_cooldown()

        if timeout is None:
            timeout = self.request_timeout

        last_exception: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                if self.is_in_cooldown:
                    self._wait_for_cooldown()
                elif attempt > 0:
                    time.sleep(self._retry_sleep_seconds(attempt - 1))

                self.stats.requests_attempted += 1
                response = self.session.get(url, headers=self._build_request_headers(), timeout=timeout)
                if response.status_code == 200:
                    self.mark_success()
                    return response

                self.stats.request_failures += 1
                if response.status_code == 429:
                    self.stats.rate_limit_responses += 1
                    self.mark_http_failure(response.status_code, response.text[:200])
                    if attempt < self.max_retries:
                        continue
                    return None

                if response.status_code in (403, 999):
                    self.mark_http_failure(response.status_code, response.text[:200])
                    return None

                if response.status_code >= 500:
                    if attempt < self.max_retries:
                        self.mark_http_failure(response.status_code, f"HTTP {response.status_code}")
                        continue
                    self.mark_http_failure(response.status_code, f"HTTP {response.status_code}")
                    return None

                logger.warning(
                    "HTTP {} from {} (non-retryable).",
                    response.status_code,
                    self.display_name,
                )
                self.stats.last_status = response.status_code
                return None
            except requests.RequestException as exc:
                self.mark_network_failure(exc)
                last_exception = exc
                if attempt >= self.max_retries:
                    logger.debug("{} request failed after retries: {}", self.display_name, last_exception)
                    return None

        return None

    def _pick_first_text(self, node: Tag, selectors: List[str], default: str = "") -> str:
        for selector in selectors:
            element = node.select_one(selector)
            if isinstance(element, Tag):
                text = self._clean_text(element.get_text()) if isinstance(element, Tag) else ""
                if text:
                    return text
        return default

    def _pick_first_attribute(self, node: Tag, selectors: List[str], attribute: str, default: str = "") -> str:
        for selector in selectors:
            element = node.select_one(selector)
            if isinstance(element, Tag):
                value = element.get(attribute)
                if value:
                    return str(value).strip()
        return default

    def _clean_text(self, text: Optional[str]) -> str:
        if not text:
            return ""
        return " ".join(str(text).split())

    DEFAULT_MAX_PAGES = 4
    RESULTS_PER_PAGE = 10

    SEARCH_URL_TEMPLATE = (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
        "keywords={keywords}"
        "&location={location}"
        "&f_TPR=r{time_filter_seconds}"
        "&f_JT=F&start={start}"
        "&origin=JOB_SEARCH_PAGE_JOB_FILTER&trk=public_jobs_jobs-search-bar_search-submit"
    )

    CARD_CONTAINER_SELECTORS = [
        "div.job-search-card",
        "div.base-card",
        "div.jobs-search__results-list > li",
    ]
    CARD_TITLE_SELECTORS = [
        "h3.base-search-card__title",
        "h3.base-search-card__title a",
        "h3.base-card__title",
        "h3.result-card__title",
        "h3.job-card-list__title",
        "h3",
    ]
    CARD_COMPANY_SELECTORS = [
        "h4.base-search-card__subtitle a",
        "h4.base-search-card__subtitle",
        "a.topcard__flavor--name",
        "span.topcard__flavor a",
        "h4",
    ]
    CARD_LOCATION_SELECTORS = [
        "span.job-search-card__location",
        "span.topcard__flavor--bullet",
        "span",
    ]
    CARD_LINK_SELECTORS = [
        "a.base-card__full-link",
        "a.job-card-container__link",
        "a.result-card__full-card-link",
        "a",
    ]
    CARD_TIME_SELECTORS = [
        "time",
        "time.job-search-card__listdate",
    ]
    def _normalize_and_validate_job(self, job: Dict[str, str]) -> Optional[Dict[str, str]]:
        title = self._clean_text(job.get("title", ""))
        company = self._clean_text(job.get("company", ""))
        source = self._clean_text(job.get("source", ""))
        url = self._normalize_url(job.get("url", ""))
        if not title or not source:
            return None
        if not company:
            return None

        record = {
            "title": title,
            "company": company,
            "location": self._clean_text(job.get("location", "")),
            "source": source,
            "url": url,
            "salary": self._clean_text(job.get("salary", "Not specified")) or "Not specified",
            "scraped_at": utc_now(),
        }
        if job.get("posted_at"):
            record["posted_at"] = self._clean_text(job.get("posted_at"))
        return record

    def _build_search_url(self, keywords: str, location: str, time_filter_seconds: int, start: int = 0) -> str:
        return self.SEARCH_URL_TEMPLATE.format(
            keywords=quote_plus(keywords),
            location=quote_plus(location),
            time_filter_seconds=time_filter_seconds,
            start=start,
        )

    def _extract_cards(self, soup: BeautifulSoup) -> List[Tag]:
        for selector in self.CARD_CONTAINER_SELECTORS:
            cards = soup.select(selector)
            if cards:
                return cards
        return []

    @staticmethod
    def _normalize_url(url: str) -> str:
        if not url:
            return ""
        value = str(url).strip()
        absolute_url = (
            value
            if value.startswith("http://") or value.startswith("https://")
            else urljoin("https://www.linkedin.com", value)
        )
        identity = normalize_job_url(absolute_url, "LinkedIn")
        if identity.startswith("linkedin:"):
            job_id = identity.removeprefix("linkedin:")
            return f"https://www.linkedin.com/jobs/view/{job_id}/"
        return absolute_url.split("?", 1)[0].split("#", 1)[0].rstrip("/")

    def scrape_jobs(
        self,
        keywords: str,
        location: str,
        time_filter_seconds: int,
        max_pages: Optional[int] = None,
        max_jobs: Optional[int] = None,
    ) -> List[Dict]:
        jobs: List[Dict] = []
        max_pages = max(1, int(max_pages)) if max_pages else self.DEFAULT_MAX_PAGES
        max_jobs = max_jobs if (max_jobs and max_jobs > 0) else None

        seen_urls: set = set()
        seen_page_fingerprints: set[tuple[str, ...]] = set()
        request_attempts_before = self.stats.requests_attempted
        request_failures_before = self.stats.request_failures
        rate_limits_before = self.stats.rate_limit_responses
        report: Dict[str, object] = {
            "source": self.display_name,
            "keyword": keywords,
            "location": location,
            "page_limit": max_pages,
            "page_offsets": [],
            "pages_attempted": 0,
            "pages_completed": 0,
            "raw_cards": 0,
            "valid_jobs": 0,
            "duplicate_cards": 0,
            "request_attempts": 0,
            "request_failures": 0,
            "rate_limit_responses": 0,
            "stop_reason": "page_limit",
            "last_status": None,
            "last_error": None,
        }
        start = 0
        for page_no in range(max_pages):
            if max_jobs is not None and len(jobs) >= max_jobs:
                report["stop_reason"] = "job_limit"
                break

            report["pages_attempted"] = int(report["pages_attempted"]) + 1
            page_offsets = report["page_offsets"]
            assert isinstance(page_offsets, list)
            page_offsets.append(start)
            search_url = self._build_search_url(keywords, location, time_filter_seconds, start=start)
            resp = self.request(search_url)
            if not resp:
                report["stop_reason"] = (
                    f"http_{self.stats.last_status}"
                    if self.stats.last_status is not None
                    else "request_failed"
                )
                break

            try:
                soup = BeautifulSoup(resp.content, "lxml")
                cards = self._extract_cards(soup)
            except Exception as exc:
                logger.debug("LinkedIn list page parse error: {}", exc)
                report["stop_reason"] = "parse_error"
                report["last_error"] = str(exc)
                break

            report["pages_completed"] = int(report["pages_completed"]) + 1
            report["raw_cards"] = int(report["raw_cards"]) + len(cards)
            if not cards:
                report["stop_reason"] = "empty_page"
                break

            fingerprint_parts: List[str] = []
            for card in cards:
                link = self._pick_first_attribute(card, self.CARD_LINK_SELECTORS, "href")
                normalized_link = self._normalize_url(link)
                if normalized_link:
                    fingerprint_parts.append(normalized_link)
                else:
                    fingerprint_parts.append(
                        "|".join(
                            (
                                self._pick_first_text(card, self.CARD_TITLE_SELECTORS),
                                self._pick_first_text(card, self.CARD_COMPANY_SELECTORS),
                            )
                        )
                    )
            # LinkedIn may reorder an otherwise identical response page. The
            # fingerprint represents the set of canonical posting identities,
            # not their presentation order.
            page_fingerprint = tuple(sorted(fingerprint_parts))
            if page_fingerprint in seen_page_fingerprints:
                report["duplicate_cards"] = int(report["duplicate_cards"]) + len(cards)
                report["stop_reason"] = "repeated_page"
                break
            seen_page_fingerprints.add(page_fingerprint)

            page_count = 0
            for card in cards:
                try:
                    if not isinstance(card, Tag):
                        continue
                    title = self._pick_first_text(card, self.CARD_TITLE_SELECTORS)
                    company = self._pick_first_text(card, self.CARD_COMPANY_SELECTORS)
                    loc = self._pick_first_text(card, self.CARD_LOCATION_SELECTORS, default=location)
                    link = self._pick_first_attribute(card, self.CARD_LINK_SELECTORS, "href")
                    posted_at = self._pick_first_attribute(card, self.CARD_TIME_SELECTORS, "datetime")

                    normalized_link = self._normalize_url(link)
                    if normalized_link and normalized_link in seen_urls:
                        report["duplicate_cards"] = int(report["duplicate_cards"]) + 1
                        continue
                    if normalized_link:
                        seen_urls.add(normalized_link)

                    normalized = self._normalize_and_validate_job(
                        {
                            "title": title,
                            "company": company,
                            "location": loc,
                            "source": self.display_name,
                            "url": normalized_link,
                            "salary": "Not specified",
                            "posted_at": posted_at,
                        }
                    )
                    if normalized:
                        normalized["query_page_offset"] = start
                        jobs.append(normalized)
                        page_count += 1
                        if max_jobs is not None and len(jobs) >= max_jobs:
                            report["stop_reason"] = "job_limit"
                            break
                except Exception as exc:
                    logger.debug("LinkedIn card parse error: {}", exc)
                    continue

            if page_count == 0:
                report["stop_reason"] = "no_new_jobs"
                break

            if len(cards) < self.RESULTS_PER_PAGE:
                report["stop_reason"] = "short_page"
                break

            start += self.RESULTS_PER_PAGE
            if page_no < max_pages - 1:
                time.sleep(max(0.5, self.config.request_delay))

        report["valid_jobs"] = len(jobs)
        report["request_attempts"] = self.stats.requests_attempted - request_attempts_before
        report["request_failures"] = self.stats.request_failures - request_failures_before
        report["rate_limit_responses"] = self.stats.rate_limit_responses - rate_limits_before
        report["last_status"] = self.stats.last_status
        report["last_error"] = self.stats.last_error
        self.last_query_report = report
        return jobs


class JobScraper:
    """Coordinate LinkedIn queries, deterministic filtering, and persistence."""

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
        self.collection_reports: List[Dict[str, object]] = []
        self.query_matches: List[Dict[str, object]] = []
        self.db = JobDatabase()
        self._linkedin_source = LinkedInSource(self.session, self.config)
        self.max_total_jobs = max(0, int(getattr(self.config, 'max_total_jobs', 0)))
        self.min_new_jobs_to_continue = max(0, int(getattr(self.config, 'min_new_jobs_to_continue', 1)))

    def get_collection_coverage(self) -> Dict[str, int]:
        """Return aggregate query/page telemetry for the current collection."""
        reports = self.collection_reports
        return {
            "queries": len(reports),
            "pages_attempted": sum(int(report.get("pages_attempted", 0) or 0) for report in reports),
            "pages_completed": sum(int(report.get("pages_completed", 0) or 0) for report in reports),
            "raw_cards": sum(int(report.get("raw_cards", 0) or 0) for report in reports),
            "valid_jobs": sum(int(report.get("valid_jobs", 0) or 0) for report in reports),
            "duplicate_cards": sum(int(report.get("duplicate_cards", 0) or 0) for report in reports),
            "request_attempts": sum(int(report.get("request_attempts", 0) or 0) for report in reports),
            "request_failures": sum(int(report.get("request_failures", 0) or 0) for report in reports),
            "rate_limit_responses": sum(int(report.get("rate_limit_responses", 0) or 0) for report in reports),
            "incomplete_queries": sum(
                1
                for report in reports
                if str(report.get("stop_reason") or "")
                in {"request_failed", "parse_error", "http_403", "http_429", "http_999"}
            ),
        }

    def _build_filter_decisions_for_jobs(
        self,
        jobs_df: pd.DataFrame,
        manual_overrides: Optional[dict[str, str]] = None,
    ) -> List[Dict[str, object]]:
        """Return audit records for jobs that current rule-based filters would archive."""
        if jobs_df.empty or 'job_id' not in jobs_df.columns:
            return []

        try:
            unwanted_keywords = self.config.get_unwanted_keywords_list()
        except Exception:
            unwanted_keywords = []
        try:
            unwanted_companies = self.config.get_unwanted_companies_list()
        except Exception:
            unwanted_companies = []

        decisions: List[Dict[str, object]] = []
        for _, row in jobs_df.iterrows():
            job_id = str(row.get('job_id') or '').strip()
            if not job_id:
                continue
            if (manual_overrides or {}).get(job_id) in {"keep", "archive"}:
                continue

            title = str(row.get('title') or '').strip()
            company = str(row.get('company') or '').strip()

            matched_keyword = match_keyword_filter(title, unwanted_keywords)
            if matched_keyword:
                decisions.append(
                    {
                        "job_id": job_id,
                        "decision_source": "rule",
                        "decision_action": "archive",
                        "filter_name": "title_keyword",
                        "matched_value": matched_keyword,
                        "reason": f"Archived by title keyword filter: {matched_keyword}",
                        "details": {"title": title, "company": company},
                    }
                )

            matched_company = match_company_filter(company, unwanted_companies)
            if matched_company:
                decisions.append(
                    {
                        "job_id": job_id,
                        "decision_source": "rule",
                        "decision_action": "archive",
                        "filter_name": "company_blacklist",
                        "matched_value": matched_company,
                        "reason": f"Archived by company blacklist match: {matched_company}",
                        "details": {"title": title, "company": company},
                    }
                )

            matched_study_pattern = match_study_title_pattern(title)
            if matched_study_pattern:
                decisions.append(
                    {
                        "job_id": job_id,
                        "decision_source": "rule",
                        "decision_action": "archive",
                        "filter_name": "study_title",
                        "matched_value": matched_study_pattern,
                        "reason": "Archived as internship or study-track role based on title",
                        "details": {"title": title, "company": company},
                    }
                )

            matched_academic_pattern = match_academic_title_pattern(title, company)
            if matched_academic_pattern and not matched_study_pattern:
                decisions.append(
                    {
                        "job_id": job_id,
                        "decision_source": "rule",
                        "decision_action": "archive",
                        "filter_name": "academic_title",
                        "matched_value": matched_academic_pattern,
                        "reason": "Archived as an academic role based on title",
                        "details": {"title": title, "company": company},
                    }
                )

        return decisions

    @staticmethod
    def _job_ids_from_filter_decisions(decisions: List[Dict[str, object]]) -> set[str]:
        return {
            str(decision.get("job_id") or "").strip()
            for decision in decisions
            if str(decision.get("job_id") or "").strip()
            and str(decision.get("decision_action") or "archive").strip().lower() == "archive"
        }

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

    def _collect_observed_jobs(
        self,
        page_jobs: List[Dict],
        existing_keys: set,
    ) -> Tuple[List[Dict], int]:
        seen_keys: set = set()
        existing_matches = 0
        observed_jobs: List[Dict] = []

        for j in page_jobs:
            key = build_normalized_key(
                source=j.get("source", "LinkedIn"),
                title=j.get("title", ""),
                company=j.get("company", ""),
                url=j.get("url", ""),
            )
            if not key:
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            job_payload = dict(j)
            observed_jobs.append(job_payload)
            if key in existing_keys:
                existing_matches += 1

        return observed_jobs, existing_matches

    def scrape_linkedin(
        self,
        keywords: str,
        location: str,
        query_spec: Optional[QuerySpec] = None,
    ) -> List[Dict]:
        """Scrape LinkedIn public listings with pagination.
        - Applies: configurable time range (day/week/month), full-time only
        - Paginates the public load-more endpoint using its 10-result offsets
        """
        time_filter_seconds, normalized_time_range = self._get_linkedin_time_filter()
        if normalized_time_range != getattr(self.config, "search_time_range", "day"):
            logger.info(
                "Normalized LinkedIn time range '{}' for keyword '{}' / location '{}'.",
                normalized_time_range,
                keywords,
                location,
            )
        if not self.config.quiet_progress:
            logger.debug(
                "Fetching LinkedIn jobs for '{}' in '{}' with f_TPR={}.",
                keywords,
                normalized_time_range,
                time_filter_seconds,
            )

        page_limit = None
        if self.max_total_jobs > 0:
            page_limit = self.max_total_jobs
        source_page_limit = getattr(self.config, "linkedin_max_search_pages", None)
        if source_page_limit is None and self.max_total_jobs > 0:
            source_page_limit = (
                (self.max_total_jobs + self._linkedin_source.RESULTS_PER_PAGE - 1)
                // self._linkedin_source.RESULTS_PER_PAGE
            )
        page_jobs = self._linkedin_source.scrape_jobs(
            keywords,
            location,
            time_filter_seconds,
            max_pages=source_page_limit or self._linkedin_source.DEFAULT_MAX_PAGES,
            max_jobs=page_limit,
        )
        report = dict(self._linkedin_source.last_query_report)
        if query_spec is not None:
            report["query_group_key"] = query_spec.group_key
            report["query_group_name"] = query_spec.display_name
        self.collection_reports.append(report)
        logger.info(
            "LinkedIn query '{}' @ '{}': {}/{} pages completed, {} cards, {} valid jobs, stop={}",
            keywords,
            location,
            int(report.get("pages_completed", 0) or 0),
            int(report.get("pages_attempted", 0) or 0),
            int(report.get("raw_cards", 0) or 0),
            int(report.get("valid_jobs", 0) or 0),
            report.get("stop_reason", "unknown"),
        )

        jobs: List[Dict] = []
        existing_keys = self._get_existing_normalized_keys()

        try:
            jobs, existing_matches = self._collect_observed_jobs(
                page_jobs=page_jobs,
                existing_keys=existing_keys,
            )
            for job in jobs:
                job["query_group_key"] = query_spec.group_key if query_spec else "custom"
                job["query_group_name"] = query_spec.display_name if query_spec else "Custom"
                job["query_text"] = keywords
                job["query_location"] = location

            logger.info(
                "LinkedIn {} @ {}: {} observed, {} already known",
                keywords,
                location,
                len(jobs),
                existing_matches,
            )
        except Exception as e:
            logger.error(f"Error scraping LinkedIn: {e}")

        return jobs

    def scrape(self, keywords: List[str], locations: List[str], *, limit_per_query: Optional[int] = None) -> pd.DataFrame:
        """Run each configured LinkedIn title/location query once."""
        self.collection_reports = []
        self.query_matches = []
        all_jobs: List[Dict] = []
        query_specs = query_specs_for_terms(keywords)
        total_queries = len(query_specs) * len(locations)
        completed_queries = 0
        stop_collecting = False
        update_api_run_progress(
            stage="collecting",
            label="Searching LinkedIn",
            completed_queries=0,
            total_queries=total_queries,
            event="Starting LinkedIn collection",
        )

        for query_spec in query_specs:
            keyword = query_spec.query_text
            for location in locations:
                if stop_collecting:
                    break
                time.sleep(self.config.request_delay)
                source_label = f"LinkedIn · {keyword} in {location}"
                update_api_run_progress(
                    stage="collecting",
                    label="Searching LinkedIn",
                    current_query=source_label,
                    completed_queries=completed_queries,
                    total_queries=total_queries,
                    metrics=_collection_progress_metrics(len(all_jobs)),
                    event=f"Searching {source_label}",
                )
                jobs = self.scrape_linkedin(keyword, location, query_spec)
                if limit_per_query is not None and limit_per_query > 0:
                    jobs = jobs[:limit_per_query]
                if self.max_total_jobs:
                    jobs = jobs[: max(0, self.max_total_jobs - len(all_jobs))]
                all_jobs.extend(jobs)
                completed_queries += 1
                if self.max_total_jobs and len(all_jobs) >= self.max_total_jobs:
                    stop_collecting = True
            if stop_collecting:
                break

        df = pd.DataFrame(all_jobs)
        if not df.empty:
            df["job_id"] = build_job_ids(df)
            candidate_count = len(df)
            match_columns = [
                "job_id",
                "query_group_key",
                "query_group_name",
                "query_text",
                "query_location",
                "query_page_offset",
            ]
            for column in match_columns[1:]:
                if column not in df.columns:
                    df[column] = 0 if column == "query_page_offset" else ""
            self.query_matches = (
                df[match_columns]
                .drop_duplicates(subset=["job_id", "query_text", "query_location"])
                .to_dict(orient="records")
            )
            df = df.drop_duplicates(subset=["job_id"], keep="first")
            logger.info(
                "Prepared {} unique observations from {} LinkedIn candidates",
                len(df),
                candidate_count,
            )
        else:
            logger.warning("No LinkedIn jobs found")

        update_api_run_progress(
            stage="storing",
            label=f"Preparing {len(df)} observed jobs for storage",
            current_query="",
            completed_queries=completed_queries,
            total_queries=total_queries,
            metrics=_collection_progress_metrics(len(df)),
            event=f"LinkedIn collection finished with {len(df)} observed jobs",
        )
        return df
    
    def execute_job_search(self) -> bool:
        """Execute job search and persist results"""
        self.last_run_failed = False
        scrape_run_id: Optional[str] = None
        progress_metrics = {
            "observed": 0,
            "new": 0,
            "archived": 0,
        }

        try:
            keywords = [k.strip() for k in self.config.search_keywords.split(',') if k.strip()]
            locations = [l.strip() for l in self.config.search_locations.split(',') if l.strip()]

            if not keywords:
                raise ValueError("No valid search keywords configured. Set SEARCH_KEYWORDS in .env or pass --keywords.")
            if not locations:
                raise ValueError("No valid search locations configured. Set SEARCH_LOCATIONS in .env or pass --locations.")
            scrape_run_id = self.db.start_scrape_run(
                mode="run-once",
                keywords=keywords,
                locations=locations,
                observed_at=utc_now_iso(),
            )
            jobs_df = self.scrape(keywords, locations)
            progress_metrics["observed"] = len(jobs_df)
            coverage = self.get_collection_coverage()
            progress_metrics.update(coverage)
            logger.info(
                "LinkedIn coverage: {} queries, {}/{} pages completed, {} request failures, {} rate limits",
                coverage["queries"],
                coverage["pages_completed"],
                coverage["pages_attempted"],
                coverage["request_failures"],
                coverage["rate_limit_responses"],
            )
            self.db.record_collection_queries(scrape_run_id, self.collection_reports)

            if jobs_df.empty:
                self.db.finish_scrape_run(
                    scrape_run_id,
                    observed_jobs_count=0,
                    new_jobs_count=0,
                    coverage=self.collection_reports,
                )
                update_api_run_progress(
                    stage="finalizing",
                    label="Finalizing collection with no observed jobs",
                    metrics=progress_metrics,
                    event="No jobs were observed during collection",
                    event_level="warning",
                )
                logger.warning("No jobs found in this search.")
                return False

            job_ids = [str(value) for value in jobs_df["job_id"].tolist()]
            manual_overrides = self.db.get_manual_overrides(job_ids)
            filter_decisions = self._build_filter_decisions_for_jobs(jobs_df, manual_overrides)
            prefilter_archive_ids = self._job_ids_from_filter_decisions(filter_decisions)

            provenance_by_job: dict[str, list[dict[str, object]]] = {}
            for match in self.query_matches:
                match_job_id = str(match.get("job_id") or "")
                provenance_by_job.setdefault(match_job_id, []).append(
                    {
                        "query_group_key": match.get("query_group_key"),
                        "query_text": match.get("query_text"),
                        "query_location": match.get("query_location"),
                    }
                )

            relevance_evaluations: List[Dict[str, object]] = []
            relevance_decisions: List[Dict[str, object]] = []
            relevance_counts: Dict[str, int] = {}
            unwanted_keywords = self.config.get_unwanted_keywords_list()
            relevance_mode = getattr(self.config, "relevance_mode", "shadow")
            include_unmatched = bool(getattr(self.config, "archive_unmatched_jobs", False))
            evaluated_at = utc_now_iso()
            for _, row in jobs_df.iterrows():
                job_id = str(row.get("job_id") or "").strip()
                result = classify_relevance(
                    str(row.get("title") or ""),
                    company=str(row.get("company") or ""),
                    unwanted_keywords=unwanted_keywords,
                    manual_action=manual_overrides.get(job_id),
                )
                evaluation = {
                    "job_id": job_id,
                    **result.to_dict(),
                    "evaluated_at": evaluated_at,
                }
                relevance_evaluations.append(evaluation)
                relevance_counts[result.outcome] = relevance_counts.get(result.outcome, 0) + 1
                details = {
                    "scrape_run_id": scrape_run_id,
                    "query_matches": provenance_by_job.get(job_id, []),
                }
                if result.outcome == "manual_archive":
                    decision = {
                        "job_id": job_id,
                        "decision_source": "manual",
                        "decision_action": "archive",
                        "filter_name": "manual_archive_enforced",
                        "matched_value": "manual",
                        "reason": result.reason,
                        "details": details,
                    }
                else:
                    decision = None
                    if not (result.outcome == "excluded" and job_id in prefilter_archive_ids):
                        decision = decision_for_result(
                            job_id,
                            result,
                            mode=relevance_mode,
                            include_unmatched=include_unmatched,
                            details=details,
                        )
                if decision is not None:
                    relevance_decisions.append(decision)

            for outcome, count in relevance_counts.items():
                progress_metrics[f"relevance_{outcome}"] = count
            distinct_count = max(1, len(jobs_df))
            progress_metrics["target_yield_percent"] = round(
                relevance_counts.get("target", 0) / distinct_count * 100
            )
            progress_metrics["query_contamination_percent"] = round(
                (
                    relevance_counts.get("unrelated", 0)
                    + relevance_counts.get("unmatched", 0)
                )
                / distinct_count
                * 100
            )
            progress_metrics["relevance_conflicts"] = sum(
                1
                for evaluation in relevance_evaluations
                if evaluation.get("positive_matches") and evaluation.get("negative_matches")
            )
            relevance_summary = {
                key: int(value)
                for key, value in progress_metrics.items()
                if key.startswith("relevance_")
                or key in {"target_yield_percent", "query_contamination_percent"}
            }
            logger.info(
                "Relevance {} using ruleset {}: {}",
                relevance_mode,
                RULESET_VERSION,
                relevance_counts,
            )

            filter_counts: Dict[str, int] = {}
            all_decisions = [*filter_decisions, *relevance_decisions]
            for decision in all_decisions:
                filter_name = str(decision.get("filter_name") or "unknown")
                if str(decision.get("decision_action") or "").lower() == "archive":
                    filter_counts[filter_name] = filter_counts.get(filter_name, 0) + 1
                details = decision.get("details")
                if not isinstance(details, dict):
                    details = {}
                details["scrape_run_id"] = scrape_run_id
                details["ruleset_version"] = RULESET_VERSION
                details["query_matches"] = provenance_by_job.get(
                    str(decision.get("job_id") or ""),
                    [],
                )
                decision["details"] = details
            archived_job_ids = self._job_ids_from_filter_decisions(all_decisions)
            active_jobs_df = jobs_df
            if archived_job_ids and 'job_id' in jobs_df.columns:
                active_jobs_df = jobs_df[~jobs_df['job_id'].astype(str).isin(archived_job_ids)].copy()
            if self.max_total_jobs:
                active_jobs_df = active_jobs_df.head(self.max_total_jobs)

            # Store jobs in SQLite database (fast deduplication)
            update_api_run_progress(
                stage="storing",
                label=f"Recording {len(jobs_df)} observed jobs",
                metrics=progress_metrics,
                event=f"Recording {len(jobs_df)} observed jobs",
            )
            _ = self.db.put_into_sql(
                jobs_df,
                scrape_run_id=scrape_run_id,
            )
            self.db.record_job_query_matches(scrape_run_id, self.query_matches)
            self.db.save_relevance_evaluations(relevance_evaluations)
            archived_count = 0
            if all_decisions:
                archive_summary = self.db.archive_jobs_with_filter_decisions(
                    all_decisions,
                    default_reason="Archived by scrape-time rule filter",
                )
                archived_count = int(archive_summary.get("archived", 0) or 0)
                progress_metrics["archived"] = archived_count
                logger.info(
                    "Archived {} filtered jobs and recorded {} decisions by rule: {}",
                    archived_count,
                    int(archive_summary.get("decisions_recorded", 0) or 0),
                    filter_counts,
                )

            self.db.finish_scrape_run(
                scrape_run_id,
                archived_jobs_count=archived_count,
                coverage=self.collection_reports,
                relevance=relevance_summary,
            )

            with self.db._get_connection() as conn:
                active_new_jobs_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM job_observations o
                        INNER JOIN jobs j ON j.job_id = o.job_id
                        WHERE o.scrape_run_id = ?
                          AND o.is_new = 1
                          AND j.archived_at IS NULL
                        """,
                        (scrape_run_id,),
                    ).fetchone()[0]
                )
            progress_metrics["new"] = active_new_jobs_count

            if active_jobs_df.empty:
                update_api_run_progress(
                    stage="finalizing",
                    label="Finalizing collection with no eligible jobs",
                    metrics=progress_metrics,
                    event="All observed jobs were archived by filters",
                    event_level="warning",
                )
                logger.warning("All scraped jobs were archived by filter criteria")
                return False

            if self.min_new_jobs_to_continue and active_new_jobs_count < self.min_new_jobs_to_continue:
                update_api_run_progress(
                    stage="finalizing",
                    label="Finalizing collection below the new-job threshold",
                    metrics=progress_metrics,
                    event="Collection is below the configured new-job threshold",
                    event_level="warning",
                )
                logger.info(
                    "Found {} new jobs, below MIN_NEW_JOBS_TO_CONTINUE={}; skipping further processing",
                    active_new_jobs_count,
                    self.min_new_jobs_to_continue,
                )
                return False

            update_api_run_progress(
                stage="finalizing",
                label="Finalizing collection results",
                metrics=progress_metrics,
                event="Finalizing collection results",
            )
            logger.info(
                "Scraping completed: {} observed jobs, {} active jobs kept, {} new active jobs stored, {} archived by filters",
                len(jobs_df),
                len(active_jobs_df),
                active_new_jobs_count,
                archived_count,
            )
            return True

        except Exception as e:
            self.last_run_failed = True
            if scrape_run_id:
                self.db.finish_scrape_run(scrape_run_id, coverage=self.collection_reports)
            update_api_run_progress(
                stage="failed",
                label="Collection stopped with an error",
                metrics=progress_metrics,
                event=f"Collection failed: {e}",
                event_level="error",
            )
            logger.error(f"Error in job search: {e}")
            return False

    def get_search_summary(self) -> dict:
        """Get summary of search configuration"""
        return {
            'keywords': self.config.get_keywords_list(),
            'locations': self.config.get_locations_list(),
            'time_range': getattr(self.config, 'search_time_range', 'day'),
            'user_agent': self.config.user_agent[:50] + "..." if len(self.config.user_agent) > 50 else self.config.user_agent,
            'request_delay': self.config.request_delay,
            'max_retries': self.config.max_retries,
            'linkedin_max_search_pages': self.config.linkedin_max_search_pages,
        }
