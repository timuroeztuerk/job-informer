"""
Job Scraper Module - Currently only LinkedIn, which my experience says is enough for now.
"""
import os
import glob
import time
import random
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import sys
import requests
from bs4 import BeautifulSoup
from bs4.element import Tag
import pandas as pd
from loguru import logger
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

from ..config.settings import Config, DEFAULT_TIME_RANGE
from ..utils.database import JobDatabase
from ..utils.data_utils import build_job_ids, build_normalized_keys, normalize_job_url, remove_historical_duplicates
from ..utils.terminal_utils import _sleep_with_feedback, _print_progress, _progress_bar, _sleep_quiet
from ..utils.filtering import normalize_text, should_filter_by_keywords, should_filter_by_company

TIME_RANGE_TO_SECONDS = {
    "day": 24 * 60 * 60,
    "week": 7 * 24 * 60 * 60,
    "month": 30 * 24 * 60 * 60,
}

TIME_RANGE_TO_DAYS = {
    "day": 1,
    "week": 7,
    "month": 30,
}


@dataclass
class ScraperSourceStats:
    consecutive_failures: int = 0
    blocked_until_ts: float = 0.0
    last_status: Optional[int] = None
    last_error: Optional[str] = None


class ScraperSource:
    """Source abstraction for one scraper backend."""

    name = "base"
    display_name = "Base Source"
    base_backoff_seconds = 4.0
    max_backoff_seconds = 240.0

    def __init__(self, session: requests.Session, config: Config):
        self.session = session
        self.config = config
        self.stats = ScraperSourceStats()
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
        if status_code in (403, 429, 999):
            logger.warning(
                "{} blocked (status={}); entering source cooldown.",
                self.display_name,
                status_code,
            )
        self.stats.blocked_until_ts = time.time() + self._failure_backoff_seconds(status_code)

    def mark_network_failure(self, exc: Exception) -> None:
        self.stats.consecutive_failures += 1
        self.stats.last_status = None
        self.stats.last_error = str(exc)
        self.stats.blocked_until_ts = time.time() + self._failure_backoff_seconds(None)

    def request(self, url: str, timeout: Optional[int] = None) -> Optional[requests.Response]:
        if self.is_in_cooldown:
            logger.warning(
                "{} is currently in cooldown ({:.1f}s left); skipping request.",
                self.display_name,
                self.cooldown_remaining,
            )
            return None

        if timeout is None:
            timeout = self.request_timeout

        last_exception: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                if attempt > 0:
                    time.sleep(self._retry_sleep_seconds(attempt - 1))

                response = self.session.get(url, headers=self._build_request_headers(), timeout=timeout)
                if response.status_code == 200:
                    self.mark_success()
                    return response

                if response.status_code in (429, 403, 999):
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

    def _looks_masked_value(self, text: str) -> bool:
        if text is None:
            return True
        value = str(text).strip()
        if not value:
            return True
        star_count = value.count("*")
        return star_count >= 4 and (star_count / max(len(value), 1)) >= 0.4

    def scrape_jobs(
        self,
        keywords: str,
        location: str,
        time_filter_seconds: int,
        max_pages: Optional[int] = None,
        max_jobs: Optional[int] = None,
    ) -> List[Dict]:
        raise NotImplementedError

    def extract_job_description(self, job_url: str) -> str:
        raise NotImplementedError

    def extract_job_metadata(self, job_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        raise NotImplementedError


class LinkedInSource(ScraperSource):
    name = "linkedin"
    display_name = "LinkedIn"
    DEFAULT_MAX_PAGES = 4
    RESULTS_PER_PAGE = 25

    SEARCH_URL_TEMPLATE = (
        "https://www.linkedin.com/jobs/search/?"
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
    METADATA_TITLE_SELECTORS = [
        "h1.top-card-layout__title",
        "h1.topcard__title",
        "h1.sub-nav-cta__header",
        "h1",
    ]
    METADATA_COMPANY_SELECTORS = [
        "a.topcard__org-name-link",
        "span.topcard__flavor a",
        "span.top-card-layout__entity-info a",
        "span.topcard__flavor",
    ]
    METADATA_LOCATION_SELECTORS = [
        "span.topcard__flavor--bullet",
        "span.top-card-layout__first-subline span",
        "span.topcard__flavor",
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
            "scraped_at": pd.Timestamp.now(),
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
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return urljoin("https://www.linkedin.com", value)

    @staticmethod
    def _extract_from_jsonld(html: Optional[str]) -> Dict[str, str]:
        if not html:
            return {}
        payload: Dict[str, str] = {}
        try:
            import json

            data = json.loads(html)
            if not isinstance(data, dict):
                return {}
            payload["title"] = str(data.get("title", "")).strip()
            payload["description"] = str(data.get("description", "")).strip()
            hiring_org = data.get("hiringOrganization")
            if isinstance(hiring_org, dict):
                payload["company"] = str(hiring_org.get("name", "")).strip()
            if isinstance(data.get("jobLocation"), dict):
                payload["location"] = str(
                    data["jobLocation"]
                    .get("address", {})
                    .get("addressLocality", "")
                ).strip()
        except Exception:
            return {}
        return payload

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
        start = 0
        for page_no in range(max_pages):
            if max_jobs is not None and len(jobs) >= max_jobs:
                break

            search_url = self._build_search_url(keywords, location, time_filter_seconds, start=start)
            resp = self.request(search_url)
            if not resp:
                break

            try:
                soup = BeautifulSoup(resp.content, "lxml")
                cards = self._extract_cards(soup)
            except Exception as exc:
                logger.debug("LinkedIn list page parse error: {}", exc)
                break

            if not cards:
                break

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
                        normalized["description"] = ""
                        jobs.append(normalized)
                        page_count += 1
                        if max_jobs is not None and len(jobs) >= max_jobs:
                            break
                except Exception as exc:
                    logger.debug("LinkedIn card parse error: {}", exc)
                    continue

            if page_count == 0:
                break

            if len(cards) < self.RESULTS_PER_PAGE:
                break

            start += self.RESULTS_PER_PAGE
            if page_no < max_pages - 1:
                time.sleep(max(0.5, self.config.request_delay))

        return jobs

    def extract_job_description(self, job_url: str) -> str:
        response = self.request(job_url, timeout=25)
        if not response:
            return ""
        try:
            page = BeautifulSoup(response.content, "lxml")
            desc = page.select_one("div.show-more-less-html__markup")
            if desc:
                return desc.get_text(separator=" ", strip=True)
            desc = page.select_one("div.description__text")
            if desc:
                return desc.get_text(separator=" ", strip=True)
            ld = page.find("script", type="application/ld+json")
            if ld and getattr(ld, "string", None):
                payload = self._extract_from_jsonld(ld.string)
                if payload.get("description"):
                    return payload.get("description", "")
        except Exception as exc:
            logger.debug(f"LinkedIn description parse error: {exc}")
        return ""

    def extract_job_metadata(self, job_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        response = self.request(job_url, timeout=25)
        if not response:
            return None, None, None
        try:
            page = BeautifulSoup(response.content, "lxml")
            title = self._pick_first_text(page, self.METADATA_TITLE_SELECTORS)
            company = self._pick_first_text(page, self.METADATA_COMPANY_SELECTORS)
            location = self._pick_first_text(page, self.METADATA_LOCATION_SELECTORS)

            if not title or not company:
                og = page.find("meta", property="og:title")
                og_content = og.get("content") if isinstance(og, Tag) else ""
                if og_content:
                    parts = str(og_content).split(" - ", 1)
                    if len(parts) == 2:
                        title = title or parts[0].strip()
                        company = company or parts[1].split("|", 1)[0].strip()

            if not title or not company:
                ld = page.find("script", type="application/ld+json")
                if ld and getattr(ld, "string", None):
                    parsed = self._extract_from_jsonld(ld.string)
                    title = title or parsed.get("title")
                    company = company or parsed.get("company")
                    location = location or parsed.get("location")

            if self._looks_masked_value(title):
                title = None
            if self._looks_masked_value(company):
                company = None
            if self._looks_masked_value(location):
                location = None

            return title, company, location
        except Exception as exc:
            logger.debug(f"LinkedIn title/company parse error: {exc}")
            return None, None, None


class IndeedSource(ScraperSource):
    name = "indeed"
    display_name = "Indeed"
    DEFAULT_MAX_PAGES = 8
    RESULTS_PER_PAGE = 15

    SEARCH_URL_TEMPLATE = (
        "https://de.indeed.com/jobs?"
        "q={keywords}"
        "&l={location}"
        "&fromage={time_filter_days}"
        "&sort=date"
        "&start={start}"
    )

    CARD_CONTAINER_SELECTORS = [
        "a.tapItem",
        "div.job_seen_beacon",
        "div.result",
        "li",
    ]
    CARD_TITLE_SELECTORS = [
        "h2.jobTitle a",
        "h2.jobTitle",
        "h2 a",
        "a.jobtitle",
        "h2",
    ]
    CARD_COMPANY_SELECTORS = [
        "span.companyName",
        "span.companyName a",
        "span.companyName span",
        "div.company_info a",
        "a.company",
        "span[data-testid='company-name']",
    ]
    CARD_LOCATION_SELECTORS = [
        "div.companyLocation",
        "div.companyLocation + div",
        "div.companyLocationAndSummary > div",
        "span.location",
    ]
    CARD_LINK_SELECTORS = [
        "a[href*='/viewjob']",
        "a.tapItem",
        "a.resultWithShelfItem a",
        "a",
    ]
    CARD_SALARY_SELECTORS = [
        "div.salary-snippet-container",
        "span.salary-snippet",
        "div.attribute_snippet",
        "div.salary",
    ]
    CARD_TIME_SELECTORS = [
        "span.date",
        "span.result-footer",
        "span[data-testid='job-age']",
    ]
    METADATA_TITLE_SELECTORS = [
        "h1.jobsearch-JobInfoHeader-title",
        "h1",
        "h1.jobsearch-ViewJobButtons-title",
        "h2.jobsearch-JobInfoHeader-title",
    ]
    METADATA_COMPANY_SELECTORS = [
        "a.icl-u-lg-mr--sm",
        "div.jobsearch-InlineCompanyRating-companyHeader > a",
        "div[data-testid='inlineHeader-companyName']",
        "div.jobsearch-CompanyInfoWithoutHeaderImage > div > div > a",
        "span.CompanyInfoWithoutHeaderImage-companyName",
    ]
    METADATA_LOCATION_SELECTORS = [
        "div.jobsearch-InlineCompanyRating-withoutCapsule div",
        "div.jobsearch-CompanyInfoWithoutHeaderImage div:first-child",
        "div.jobsearch-JobInfoHeader-subtitle",
        "div[data-testid='jobsearch-JobInfoHeader-companyLocation']",
        "div.jobsearch-CompanyInfoContainer div:nth-of-type(2)",
    ]

    @staticmethod
    def _build_search_url(
        keywords: str,
        location: str,
        time_filter_days: int,
        start: int = 0,
    ) -> str:
        return IndeedSource.SEARCH_URL_TEMPLATE.format(
            keywords=quote_plus(keywords),
            location=quote_plus(location),
            time_filter_days=max(1, int(time_filter_days)),
            start=max(0, int(start)),
        )

    @staticmethod
    def _extract_indeed_job_id(job_url: str) -> Optional[str]:
        try:
            parsed = urlparse(job_url)
            params = parse_qs(parsed.query)
            job_ids = params.get("jk")
            if job_ids and job_ids[0]:
                return str(job_ids[0]).strip()
        except Exception:
            pass
        return None

    @staticmethod
    def _normalize_url(url: str) -> str:
        if not url:
            return ""
        value = str(url).strip()
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return urljoin("https://de.indeed.com", value)

    def _build_search_url_for_request(
        self,
        keywords: str,
        location: str,
        time_filter_days: int,
        start: int = 0,
    ) -> str:
        return self._build_search_url(keywords, location, time_filter_days, start)

    def _extract_cards(self, soup: BeautifulSoup) -> List[Tag]:
        for selector in self.CARD_CONTAINER_SELECTORS:
            cards = soup.select(selector)
            if cards:
                return cards
        return []

    def scrape_jobs(
        self,
        keywords: str,
        location: str,
        time_filter_days: int,
        max_pages: Optional[int] = None,
        max_jobs: Optional[int] = None,
    ) -> List[Dict]:
        jobs: List[Dict] = []
        max_pages = max(1, int(max_pages)) if max_pages else self.DEFAULT_MAX_PAGES
        max_jobs = max_jobs if (max_jobs and max_jobs > 0) else None

        seen_urls: set = set()
        start = 0
        for page_no in range(max_pages):
            if max_jobs is not None and len(jobs) >= max_jobs:
                break

            search_url = self._build_search_url_for_request(keywords, location, time_filter_days, start=start)
            resp = self.request(search_url)
            if not resp:
                break

            try:
                soup = BeautifulSoup(resp.content, "lxml")
                cards = self._extract_cards(soup)
            except Exception as exc:
                logger.debug("Indeed list page parse error: {}", exc)
                break

            if not cards:
                break

            page_count = 0
            for card in cards:
                try:
                    if not isinstance(card, Tag):
                        continue

                    # Skip container-level nodes that are actually navigation wrappers
                    if card.name != "a" and card.name != "div" and card.name != "li":
                        continue

                    title = self._pick_first_text(card, self.CARD_TITLE_SELECTORS)
                    company = self._pick_first_text(card, self.CARD_COMPANY_SELECTORS)
                    loc = self._pick_first_text(card, self.CARD_LOCATION_SELECTORS, default=location)
                    link = self._pick_first_attribute(card, self.CARD_LINK_SELECTORS, "href")
                    salary = self._pick_first_text(card, self.CARD_SALARY_SELECTORS, default="Not specified")
                    posted_at = self._pick_first_text(card, self.CARD_TIME_SELECTORS)

                    normalized_link = self._normalize_url(link)
                    if normalized_link and normalized_link in seen_urls:
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
                            "salary": salary,
                            "posted_at": posted_at,
                        }
                    )
                    if not normalized:
                        continue

                    normalized["salary"] = salary or normalized.get("salary", "Not specified")
                    jobs.append(normalized)
                    page_count += 1

                    if max_jobs is not None and len(jobs) >= max_jobs:
                        break
                except Exception as exc:
                    logger.debug("Indeed card parse error: {}", exc)
                    continue

            if page_count == 0:
                break

            start += self.RESULTS_PER_PAGE
            if page_no < max_pages - 1:
                time.sleep(max(0.5, self.config.request_delay))

        return jobs

    def _normalize_and_validate_job(self, job: Dict[str, str]) -> Optional[Dict[str, str]]:
        title = self._clean_text(job.get("title", ""))
        company = self._clean_text(job.get("company", ""))
        source = self._clean_text(job.get("source", ""))
        url = self._normalize_url(job.get("url", ""))
        if not title or not source:
            return None
        if not company:
            return None
        return {
            "title": title,
            "company": company,
            "location": self._clean_text(job.get("location", "")),
            "source": source,
            "url": url,
            "salary": self._clean_text(job.get("salary", "Not specified")) or "Not specified",
            "scraped_at": pd.Timestamp.now(),
            "posted_at": self._clean_text(job.get("posted_at", "")),
        }

    def extract_job_description(self, job_url: str) -> str:
        response = self.request(job_url, timeout=25)
        if not response:
            return ""
        try:
            page = BeautifulSoup(response.content, "lxml")
            desc = page.select_one("div#jobDescriptionText")
            if desc:
                return desc.get_text(separator=" ", strip=True)
            desc = page.select_one("div.jobsearch-JobComponent-description")
            if desc:
                return desc.get_text(separator=" ", strip=True)
            ld = page.find("script", type="application/ld+json")
            if ld and getattr(ld, "string", None):
                payload = self._extract_from_jsonld(ld.string)
                if payload.get("description"):
                    return payload.get("description", "")
        except Exception as exc:
            logger.debug(f"Indeed description parse error: {exc}")
        return ""

    def extract_job_metadata(self, job_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        response = self.request(job_url, timeout=25)
        if not response:
            return None, None, None
        try:
            page = BeautifulSoup(response.content, "lxml")
            title = self._pick_first_text(page, self.METADATA_TITLE_SELECTORS)
            company = self._pick_first_text(page, self.METADATA_COMPANY_SELECTORS)
            location = self._pick_first_text(page, self.METADATA_LOCATION_SELECTORS)

            if not title or not company:
                og = page.find("meta", property="og:title")
                og_content = og.get("content") if isinstance(og, Tag) else ""
                if og_content:
                    parts = str(og_content).split(" at ", 1)
                    if len(parts) == 2:
                        title = title or parts[0].strip()
                        company = company or parts[1].strip()

            if not title or not company:
                ld = page.find("script", type="application/ld+json")
                if ld and getattr(ld, "string", None):
                    parsed = self._extract_from_jsonld(ld.string)
                    title = title or parsed.get("title")
                    company = company or parsed.get("company")
                    location = location or parsed.get("location")

            if self._looks_masked_value(title):
                title = None
            if self._looks_masked_value(company):
                company = None
            if self._looks_masked_value(location):
                location = None

            return title, company, location
        except Exception as exc:
            logger.debug(f"Indeed title/company parse error: {exc}")
            return None, None, None


class JobScraper:
    """Base class for job scraping functionality"""
    _sleep_with_feedback = _sleep_with_feedback
    _print_progress = _print_progress
    _progress_bar = _progress_bar
    _sleep_quiet = _sleep_quiet
    build_normalized_keys = build_normalized_keys
    remove_historical_duplicates = remove_historical_duplicates

    def _update_progress(self, completed: int, total: int, prefix: str = "") -> None:
        """Render a simple single-line progress bar in the terminal."""
        try:
            if not sys.stdout.isatty():
                label = prefix.rstrip()
                if label:
                    label = f"{label} "
                if total <= 0:
                    logger.info(f"{label}{completed}/{total}")
                    return
                step = max(1, total // 10)
                if completed == 0 or completed >= total or completed % step == 0:
                    logger.info(f"{label}{completed}/{total}")
                return
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
        self._apply_indeed_session_cookies()
        self._sources = self._build_sources()
        self._linkedin_source = self._sources.get("linkedin")
        self._indeed_source = self._sources.get("indeed")
        # Defaults
        self.request_timeout = getattr(self.config, 'request_timeout', 20)
        self._wait_tick_seconds = 1.0
        self._last_progress_line = ""
        self.max_total_jobs = max(0, int(getattr(self.config, 'max_total_jobs', 0)))
        self.min_new_jobs_to_continue = max(0, int(getattr(self.config, 'min_new_jobs_to_continue', 1)))

    def _build_sources(self) -> Dict[str, ScraperSource]:
        sources: Dict[str, ScraperSource] = {}
        if getattr(self.config, 'enable_linkedin', True):
            sources["linkedin"] = LinkedInSource(self.session, self.config)
        if getattr(self.config, 'enable_indeed', False):
            sources["indeed"] = IndeedSource(self.session, self.config)
        return sources

    def _get_source(self, source_name: str = "linkedin") -> Optional[ScraperSource]:
        return self._sources.get(source_name.lower())

    def _clean_text_field(self, text: str) -> str:
        """Normalize whitespace and strip text fields."""
        if text is None:
            return ""
        return " ".join(str(text).split())

    def _looks_masked_value(self, text: str) -> bool:
        """Detect obviously masked values such as '***********'."""
        if text is None:
            return True
        value = str(text).strip()
        if not value:
            return True
        star_count = value.count('*')
        return star_count >= 4 and (star_count / max(len(value), 1)) >= 0.4

    def _normalize_text(self, text: str) -> str:
        """Normalize text for consistent matching (handles unicode, case, whitespace)."""
        return normalize_text(text)

    def _should_filter_by_keywords(self, title: str) -> bool:
        """Centralized keyword filtering logic with word boundaries for specificity."""
        unwanted = self.config.get_unwanted_keywords_list()
        return should_filter_by_keywords(title, unwanted)

    def _should_filter_by_company(self, company: str) -> bool:
        """Centralized company filtering logic."""
        try:
            unwanted_companies = self.config.get_unwanted_companies_list()
        except Exception:
            return False
        return should_filter_by_company(company, unwanted_companies)

    def purge_keywords(self, title: str) -> bool:
        """Check for unwanted keywords in a job title using word boundaries for specificity (case-insensitive)."""
        return self._should_filter_by_keywords(title)

    def purge_companies(self, company: str) -> bool:
        """Check if company name contains any unwanted company substring (case-insensitive)."""
        return self._should_filter_by_company(company)

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
        source = self._get_source("linkedin")
        if not source:
            return None
        return source.request(url, timeout=timeout)

    def _apply_indeed_session_cookies(self) -> None:
        raw_cookie = str(getattr(self.config, 'indeed_session_cookies', '') or '').strip()
        if not raw_cookie:
            return
        applied = 0
        for token in raw_cookie.split(";"):
            if "=" not in token:
                continue
            name, value = token.split("=", 1)
            name = name.strip()
            if not name:
                continue
            self.session.cookies.set(name=name.strip(), value=value.strip(), domain=".indeed.com", path="/")
            applied += 1
        if applied:
            logger.info("Applied {} Indeed cookie value(s) from INDEED_SESSION_COOKIES", applied)

    @staticmethod
    def _time_range_to_days(normalized_time_range: str) -> int:
        return TIME_RANGE_TO_DAYS.get((normalized_time_range or DEFAULT_TIME_RANGE).strip().lower(), TIME_RANGE_TO_DAYS[DEFAULT_TIME_RANGE])

    def _get_indeed_time_filter(self) -> Tuple[int, str]:
        configured = getattr(self.config, 'search_time_range', DEFAULT_TIME_RANGE)
        normalized = (configured or DEFAULT_TIME_RANGE).strip().lower()
        if normalized not in TIME_RANGE_TO_SECONDS:
            logger.warning(
                "Unsupported time range '{}' provided; defaulting to '{}'",
                configured,
                DEFAULT_TIME_RANGE,
            )
            normalized = DEFAULT_TIME_RANGE
        return self._time_range_to_days(normalized), normalized

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
        if not self._linkedin_source:
            return ""
        return self._linkedin_source.extract_job_description(job_url)

    def _extract_indeed_description(self, job_url: str) -> str:
        """Extract description from an Indeed job page. Used by backfill mode only."""
        if not self._indeed_source:
            return ""
        return self._indeed_source.extract_job_description(job_url)

    def _extract_linkedin_job_meta(self, job_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Fetch a LinkedIn job page and return cleaned (title, company, location)."""
        if not self._linkedin_source:
            return None, None, None
        return self._linkedin_source.extract_job_metadata(job_url)

    def _extract_indeed_job_meta(self, job_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Fetch an Indeed job page and return cleaned (title, company, location)."""
        if not self._indeed_source:
            return None, None, None
        return self._indeed_source.extract_job_metadata(job_url)

    def _extract_job_description_from_url(self, source: str, job_url: str) -> str:
        source_name = (source or "").strip().lower()
        if source_name == "linkedin":
            return self._extract_linkedin_description(job_url)
        if source_name == "indeed":
            return self._extract_indeed_description(job_url)
        return ""

    def _fetch_job_metadata_from_url(self, url: str, source: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Dispatch to source-specific metadata extraction."""
        if (source or '').strip().lower() == 'linkedin':
            return self._extract_linkedin_job_meta(url)
        if (source or '').strip().lower() == 'indeed':
            return self._extract_indeed_job_meta(url)
        return None, None, None

    def _enrich_jobs_with_descriptions(self, jobs: List[Dict]) -> int:
        """Fetch full descriptions for a list of fresh jobs, bounded by config."""
        if not jobs:
            return 0
        if not bool(getattr(self.config, 'scrape_descriptions_on_search', False)):
            return 0

        limit = int(getattr(self.config, 'scrape_descriptions_limit', 0))
        if limit < 0:
            limit = 0
        # Preserve old behavior when limit is 0: enrich all candidates
        candidates = jobs if not limit else jobs[:limit]
        if not candidates:
            return 0

        fetched = 0
        self._update_progress(0, len(candidates), prefix="Description fetch: ")
        for idx, job in enumerate(candidates):
            job_url = str(job.get('url') or '').strip()
            job_id = str(job.get('job_id') or '')
            if not job_url:
                logger.debug("Skipping description fetch for job_id {}: missing URL", job_id)
                self._update_progress(idx + 1, len(candidates), prefix="Description fetch: ")
                continue

            try:
                source = str(job.get('source') or 'LinkedIn').strip()
                description = self._extract_job_description_from_url(source, job_url)
                if description:
                    job['description'] = self._clean_text_field(description)
                    fetched += 1
                else:
                    job['description'] = ''
            except Exception as e:
                logger.debug("Failed to fetch description for job {}: {}", job_id, e)
                job['description'] = ''

            time.sleep(max(0.3, self.config.request_delay))
            self._update_progress(idx + 1, len(candidates), prefix="Description fetch: ")

        logger.info("Fetched descriptions for %d/%d scraped jobs", fetched, len(candidates))
        return fetched

    def _parse_scraped_jobs(self, jobs_df: pd.DataFrame) -> int:
        """Parse scraped job descriptions immediately in the current run."""
        if jobs_df.empty:
            return 0
        if not bool(getattr(self.config, 'enable_description_parser', False)):
            return 0
        if not bool(getattr(self.config, 'scrape_descriptions_on_search', False)):
            return 0
        if not getattr(self.config, 'openai_api_key', '').strip():
            logger.info("Scrape-time description parsing skipped: OPENAI_API_KEY is not set")
            return 0

        parse_candidates = jobs_df.loc[jobs_df['description'].astype(str).str.strip() != ""].copy()
        if parse_candidates.empty:
            return 0

        from .parser import DescriptionTools

        parser = DescriptionTools(config=self.config, db=self.db)
        parsed = parser.parse_jobs_dataframe(
            parse_candidates,
            batch_size=int(getattr(self.config, 'desc_parser_batch_size', 25)),
            max_batches=int(getattr(self.config, 'desc_parser_max_batches', 4)),
            context_label="scraped jobs",
        )
        return parsed

    def _collect_fresh_jobs(
        self,
        page_jobs: List[Dict],
        source: str,
        existing_keys: set,
    ) -> Tuple[List[Dict], int, int]:
        seen_keys: set = set()
        skipped_unwanted = 0
        skipped_existing = 0
        new_jobs: List[Dict] = []

        for j in page_jobs:
            if self.purge_keywords(j.get('title', '')) or self.purge_companies(j.get('company', '')):
                skipped_unwanted += 1
                continue
            key = self._build_normalized_key_from_fields(
                j.get('source', source),
                j.get('title', ''),
                j.get('company', ''),
                j.get('url', '')
            )
            if not key:
                continue
            if key in existing_keys or key in seen_keys:
                skipped_existing += 1
                continue
            seen_keys.add(key)
            new_jobs.append(j)

        return new_jobs, skipped_unwanted, skipped_existing

    def scrape_linkedin(self, keywords: str, location: str) -> List[Dict]:
        """Scrape job postings from LinkedIn public listings with pagination and optional descriptions.
        - Applies: configurable time range (day/week/month), full-time only
        - Paginates using the `start` parameter (25 results per page typical)
        - Optionally fetches job descriptions from detail pages (bounded concurrency)
        """
        if not self._linkedin_source:
            logger.debug("LinkedIn source is disabled in this run")
            return []

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

        jobs: List[Dict] = []
        existing_keys = self._get_existing_normalized_keys()

        try:
            jobs, skipped_unwanted, skipped_existing = self._collect_fresh_jobs(
                page_jobs=page_jobs,
                source='LinkedIn',
                existing_keys=existing_keys,
            )

            # Progress for a single page
            if not getattr(self.config, 'quiet_progress', True) or sys.stdout.isatty():
                self._print_progress(
                    prefix=f"LinkedIn {keywords} @ {location}",
                    current=1,
                    total=1,
                    suffix=f"New:{len(jobs)} Unwanted:{skipped_unwanted} Existing:{skipped_existing}"
                )

            for j in jobs:
                j['description'] = ''
            self._enrich_jobs_with_descriptions(jobs)
        except Exception as e:
            logger.error(f"Error scraping LinkedIn: {e}")

        return jobs

    def scrape_indeed(self, keywords: str, location: str) -> List[Dict]:
        """Scrape job postings from Indeed with pagination and optional descriptions."""
        if not self._indeed_source:
            logger.debug("Indeed source is disabled in this run")
            return []

        indeed_filter_days, normalized_time_range = self._get_indeed_time_filter()
        if normalized_time_range != getattr(self.config, "search_time_range", "day"):
            logger.info(
                "Normalized Indeed time range '{}' for keyword '{}' / location '{}'.",
                normalized_time_range,
                keywords,
                location,
            )
        if not self.config.quiet_progress:
            logger.debug(
                "Fetching Indeed jobs for '{}' in '{}' with fromage={}.",
                keywords,
                normalized_time_range,
                indeed_filter_days,
            )

        page_limit = None
        if self.max_total_jobs > 0:
            page_limit = self.max_total_jobs
        source_page_limit = getattr(self.config, "indeed_max_search_pages", None)
        if source_page_limit is None and self.max_total_jobs > 0:
            source_page_limit = (
                (self.max_total_jobs + self._indeed_source.RESULTS_PER_PAGE - 1)
                // self._indeed_source.RESULTS_PER_PAGE
            )
        page_jobs = self._indeed_source.scrape_jobs(
            keywords,
            location,
            indeed_filter_days,
            max_pages=source_page_limit or self._indeed_source.DEFAULT_MAX_PAGES,
            max_jobs=page_limit,
        )

        jobs: List[Dict] = []
        existing_keys = self._get_existing_normalized_keys()

        try:
            jobs, skipped_unwanted, skipped_existing = self._collect_fresh_jobs(
                page_jobs=page_jobs,
                source='Indeed',
                existing_keys=existing_keys,
            )

            if not getattr(self.config, 'quiet_progress', True) or sys.stdout.isatty():
                self._print_progress(
                    prefix=f"Indeed {keywords} @ {location}",
                    current=1,
                    total=1,
                    suffix=f"New:{len(jobs)} Unwanted:{skipped_unwanted} Existing:{skipped_existing}"
                )

            for j in jobs:
                j['description'] = ''
            self._enrich_jobs_with_descriptions(jobs)
        except Exception as e:
            logger.error(f"Error scraping Indeed: {e}")

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

                if self.config.enable_indeed:
                    indeed_jobs = self.scrape_indeed(keyword, location)
                    if limit_per_source is not None and limit_per_source > 0:
                        indeed_jobs = indeed_jobs[:limit_per_source]

                    if self.max_total_jobs:
                        remaining_slots = self.max_total_jobs - len(all_jobs)
                        if remaining_slots <= 0:
                            stop_collecting = True
                            break
                        if len(indeed_jobs) > remaining_slots:
                            indeed_jobs = indeed_jobs[:remaining_slots]
                            stop_collecting = True
                    all_jobs.extend(indeed_jobs)
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
            # Filter out jobs with unwanted keywords in title using centralized logic
            df = self.filter_scraped_jobs(df)
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

    def backfill_masked_titles(self, limit: int = 100) -> dict:
        """Refetch job titles/companies/locations for records that look masked (e.g., ********)."""
        masked = self.db.get_jobs_with_masked_titles(limit=limit)
        if masked.empty:
            logger.info("No jobs with masked titles/companies/locations found to backfill")
            return {"checked": 0, "updated": 0, "failed": 0}

        updates: List[Dict[str, str]] = []
        failures: List[str] = []
        total = len(masked)
        self._update_progress(0, total, prefix="Refetch titles: ")

        for idx, row in masked.iterrows():
            job_id = str(row.get('job_id'))
            url = str(row.get('url') or '').strip()
            source = str(row.get('source') or '').strip()

            if not url:
                failures.append(job_id)
                self._update_progress(idx + 1, total, prefix="Refetch titles: ")
                continue

            title, company, location = self._fetch_job_metadata_from_url(url, source)

            # Preserve existing non-masked values if new ones not found
            existing_company = self._clean_text_field(row.get('company', ''))
            existing_location = self._clean_text_field(row.get('location', ''))
            if not company and existing_company and not self._looks_masked_value(existing_company):
                company = existing_company
            if not location and existing_location and not self._looks_masked_value(existing_location):
                location = existing_location

            if title and not self._looks_masked_value(title):
                normalized_key = normalize_job_url(url, source) or (
                    f"{source.lower()}|{title.lower()}|{(company or '').lower()}"
                )
                updates.append({
                    'job_id': job_id,
                    'title': title,
                    'company': company or '',
                    'location': location or '',
                    'normalized_key': normalized_key,
                })
            else:
                failures.append(job_id)

            time.sleep(max(0.5, self.config.request_delay))
            self._update_progress(idx + 1, total, prefix="Refetch titles: ")

        updated = self.db.update_titles_and_companies(updates) if updates else 0
        logger.info(
            "Title/company backfill complete: checked %d, updated %d, failed %d",
            total, updated, len(failures)
        )
        if failures and len(failures) <= 5:
            logger.debug("Backfill failures for job_ids: %s", failures)
        return {"checked": total, "updated": updated, "failed": len(failures)}

    def execute_job_search(self) -> bool:
        """Execute job search and persist results"""
        from ..agents.email_sender import EmailSender

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
            keywords = [k.strip() for k in self.config.search_keywords.split(',') if k.strip()]
            locations = [l.strip() for l in self.config.search_locations.split(',') if l.strip()]

            if not keywords:
                raise ValueError("No valid search keywords configured. Set SEARCH_KEYWORDS in .env or pass --keywords.")
            if not locations:
                raise ValueError("No valid search locations configured. Set SEARCH_LOCATIONS in .env or pass --locations.")
            jobs_df = self.scrape(keywords, locations)

            if jobs_df.empty:
                logger.warning("No jobs found in this search.")
                return False

            # Apply additional filtering to newly scraped jobs
            jobs_df = self.filter_scraped_jobs(jobs_df)
            if self.max_total_jobs:
                jobs_df = jobs_df.head(self.max_total_jobs)

            if jobs_df.empty:
                logger.warning("All scraped jobs were filtered out by purge criteria")
                return False

            # Store jobs in SQLite database (fast deduplication)
            new_jobs_count = self.db.put_into_sql(jobs_df)
            if new_jobs_count:
                parsed_count = self._parse_scraped_jobs(jobs_df)
                if parsed_count:
                    logger.info("Parsed %d freshly scraped descriptions in this run", parsed_count)

            if self.min_new_jobs_to_continue and new_jobs_count < self.min_new_jobs_to_continue:
                self.last_run_threshold_hit = True
                logger.info(
                    "Found %d new jobs, below MIN_NEW_JOBS_TO_CONTINUE=%d; skipping further processing",
                    new_jobs_count,
                    self.min_new_jobs_to_continue,
                )
                return False

            # Save CSV for backup/auditing unless dry-run
            csv_filename = None
            if not self.config.dry_run:
                csv_filename = self.save_jobs_to_csv(jobs_df)
                logger.info("Saved snapshot of scraped jobs to %s", csv_filename)
            else:
                logger.info("DRY_RUN is enabled; skipping CSV save")

            logger.info(
                "Scraping completed: %d new jobs stored%s",
                new_jobs_count,
                f" (CSV saved to {csv_filename})" if csv_filename else ""
            )
            return True

        except Exception as e:
            logger.error(f"Error in job search: {e}")
            # Send error notification
            try:
                EmailSender(self.config).send_error_notification(str(e))
            except Exception as notify_error:
                logger.error(f"Failed to send error notification email: {notify_error}")
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
            'enable_indeed': self.config.enable_indeed,
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
            
            # 1) Remove jobs matching unwanted keywords in TITLE with centralized logic
            unwanted_kw = self.config.get_unwanted_keywords_list()
            if unwanted_kw and 'title' in jobs_df.columns:
                mask_keep = ~jobs_df['title'].apply(self._should_filter_by_keywords)
                jobs_df = jobs_df[mask_keep]
                    
            # 2) Remove jobs matching unwanted companies with centralized logic
            unwanted_companies = self.config.get_unwanted_companies_list()
            if unwanted_companies and 'company' in jobs_df.columns:
                mask_keep = ~jobs_df['company'].apply(self._should_filter_by_company)
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

            # 1) Remove jobs matching unwanted keywords in TITLE only with centralized logic
            import re as _re
            unwanted_kw = self.config.get_unwanted_keywords_list()
            if unwanted_kw and 'title' in all_jobs_df.columns:
                mask_title_unwanted = all_jobs_df['title'].apply(self._should_filter_by_keywords)
                unwanted_by_title = all_jobs_df[mask_title_unwanted]
            else:
                unwanted_by_title = all_jobs_df.iloc[0:0]

            # Unwanted by company names (COMPANY only, case-insensitive substring with centralized logic)
            unwanted_companies = self.config.get_unwanted_companies_list()
            if unwanted_companies and 'company' in all_jobs_df.columns:
                mask_company_unwanted = all_jobs_df['company'].apply(self._should_filter_by_company)
                unwanted_by_company = all_jobs_df[mask_company_unwanted]
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
