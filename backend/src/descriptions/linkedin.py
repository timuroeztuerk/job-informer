"""Bounded public-page retrieval and deterministic LinkedIn parsing.

No credentials, redirect following, or alternate endpoints on access failure.
Saved bytes can be parsed again independently of the network client.
"""

from dataclasses import dataclass
from datetime import timedelta
from email.utils import parsedate_to_datetime
import hashlib
import json
import re
import time
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
import requests

from ..utils.time_utils import utc_now
from ..utils.data_utils import normalize_job_url

EXTRACTOR = "linkedin_public_job"
EXTRACTOR_VERSION = "1.0.0"
SCHEMA_VERSION = "1"
MAX_BODY_BYTES = 2_000_000


class DescriptionError(Exception):
    def __init__(self, kind: str, message: str, *, http_status=None, retry_after=None):
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status
        self.retry_after = retry_after


def source_url(job_id: str) -> str:
    identity = job_id if job_id.startswith("linkedin:") else normalize_job_url(job_id, "LinkedIn")
    match = re.fullmatch(r"linkedin:([0-9]{1,20})", identity)
    if not match:
        raise ValueError("Description retrieval requires a canonical LinkedIn posting ID.")
    return f"https://www.linkedin.com/jobs/view/{match[1]}/"


def _text(node) -> str:
    """Keep paragraph/list boundaries without splitting inline emphasis."""
    soup = BeautifulSoup(str(node), "lxml")
    for tag in soup.select("script, style, noscript, iframe, button"):
        tag.decompose()
    for tag in soup.find_all("br"):
        tag.replace_with("\n")
    for tag in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "ul", "ol"]):
        if tag.name == "li":
            tag.insert_before("\n• ")
        else:
            tag.insert_before("\n")
        tag.insert_after("\n")
    lines = [re.sub(r"[\t\r\f\v \xa0]+", " ", line).strip() for line in soup.get_text().split("\n")]
    return "\n".join(line for line in lines if line)


def parse_description(body: bytes, job_id: str) -> dict:
    """Schema 1: original text, explicit criteria, and evidence locations."""
    expected = source_url(job_id)
    posting_id = expected.rstrip("/").rsplit("/", 1)[1]
    soup = BeautifulSoup(body, "lxml")
    canonical = soup.select_one('link[rel="canonical"]')
    identity_confirmed = False
    if canonical and canonical.get("href"):
        canonical_url = urlparse(str(canonical["href"]))
        path = canonical_url.path
        match = re.search(r"/jobs/view/(?:[^/]*-)?([0-9]+)/?", path)
        if not canonical_url.hostname or not (canonical_url.hostname == "linkedin.com" or canonical_url.hostname.endswith(".linkedin.com")) or not match or match[1] != posting_id:
            raise DescriptionError("identity_mismatch", "LinkedIn returned a different posting or a non-job page.")
        identity_confirmed = True
    identity = soup.select_one('[data-semaphore-content-urn^="urn:li:jobPosting:"]')
    if identity:
        if identity.get("data-semaphore-content-urn") != f"urn:li:jobPosting:{posting_id}":
            raise DescriptionError("identity_mismatch", "The returned posting does not match this job.")
        identity_confirmed = True
    node = soup.select_one(".show-more-less-html__markup")
    description_text = _text(node) if node else ""
    locator = ".show-more-less-html__markup"
    # Only use a JobPosting with an explicit matching URL/identifier, never a
    # related posting embedded elsewhere in the page.
    if not description_text:
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(script.get_text())
            except (ValueError, TypeError):
                continue
            objects = payload if isinstance(payload, list) else [payload]
            objects = [item for obj in objects for item in (obj.get("@graph", [obj]) if isinstance(obj, dict) else [])]
            for obj in objects:
                if not isinstance(obj, dict) or obj.get("@type") != "JobPosting":
                    continue
                identifier = obj.get("identifier")
                identifier = identifier.get("value") if isinstance(identifier, dict) else identifier
                url = str(obj.get("url", ""))
                url_match = re.search(r"/jobs/view/(?:[^/]*-)?([0-9]+)/?(?:\?|$)", url)
                if str(identifier) != posting_id and (not url_match or url_match[1] != posting_id):
                    continue
                if isinstance(obj.get("description"), str):
                    description_text = _text(obj["description"])
                    locator = "JobPosting.description"
                    identity_confirmed = True
                    break
            if description_text:
                break
    if not description_text:
        title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
        blocked = any(word in title for word in ("sign in", "login", "security", "verification", "authwall"))
        raise DescriptionError("blocked" if blocked else "parse", "LinkedIn requires access verification." if blocked else "No original job description was found in the page.")
    # Identity also guards pages that contain a login wall and unrelated cards.
    if not identity_confirmed:
        raise DescriptionError("identity_mismatch", "The page did not identify the requested posting.")
    criteria = []
    keys = {"seniority level": "seniority", "employment type": "employment_type",
            "job function": "job_function", "industries": "industries",
            "karrierestufe": "seniority", "beschäftigungsverhältnis": "employment_type",
            "tätigkeitsbereich": "job_function", "branchen": "industries"}
    for item in soup.select(".description__job-criteria-item"):
        label = item.select_one(".description__job-criteria-subheader")
        value = item.select_one(".description__job-criteria-text")
        if label and value:
            label_text = label.get_text(" ", strip=True)
            value_text = value.get_text(" ", strip=True)
            if value_text:
                criteria.append({"key": keys.get(label_text.casefold(), "other"), "label": label_text,
                                 "value": value_text, "evidence": {"selector": ".description__job-criteria-item", "text": value_text}})
    title = soup.select_one(".topcard__title")
    return {"description_text": description_text, "criteria": criteria,
            "title": title.get_text(" ", strip=True) if title else None,
            "evidence": {"source_url": expected, "description_locator": locator}}


def extraction_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class FetchedSource:
    status: int
    body: bytes = b""
    content_type: str = "text/html"
    etag: str | None = None
    last_modified: str | None = None


def _cooldown(value: str | None) -> str:
    now = utc_now()
    seconds = 300.0
    if value:
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - now).total_seconds()
            except (ValueError, TypeError, OverflowError):
                pass
    return (now + timedelta(seconds=max(60, min(seconds, 86400)))).isoformat()


def fetch_source(job_id: str, previous: dict | None = None) -> FetchedSource:
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9", "Accept": "text/html"}
    if previous:
        if previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        if previous.get("last_modified"):
            headers["If-Modified-Since"] = previous["last_modified"]
    url = source_url(job_id)
    try:
        with requests.get(url, headers=headers, timeout=(5, 15), stream=True, allow_redirects=False) as response:
            code = response.status_code
            if code == 304 and previous:
                return FetchedSource(status=304)
            if 300 <= code < 400:
                target = urlparse(urljoin(url, response.headers.get("Location") or ""))
                if (target.scheme == "https" and target.hostname
                        and (target.hostname == "linkedin.com" or target.hostname.endswith(".linkedin.com"))
                        and target.path.startswith("/jobs/")
                        and "expired_jd_redirect" in parse_qs(target.query).get("trk", [])):
                    raise DescriptionError("not_found", "This posting has expired and is no longer publicly available.",
                                           http_status=code)
            if code in {401, 403, 429, 999} or 300 <= code < 400:
                raise DescriptionError("rate_limited" if code == 429 else "blocked",
                                       "LinkedIn rate limited retrieval." if code == 429 else "LinkedIn denied or redirected this request.",
                                       http_status=code, retry_after=_cooldown(response.headers.get("Retry-After")))
            if code in {404, 410}:
                raise DescriptionError("not_found", "This posting is no longer publicly available.", http_status=code)
            if code != 200:
                raise DescriptionError("http", f"LinkedIn returned HTTP {code}.", http_status=code)
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                raise DescriptionError("content_type", "LinkedIn returned an unexpected content type.", http_status=code)
            chunks, size, started = [], 0, time.monotonic()
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BODY_BYTES or time.monotonic() - started > 30:
                    raise DescriptionError("response_limit", "The response exceeded the retrieval size or time limit.", http_status=code)
                chunks.append(chunk)
            return FetchedSource(code, b"".join(chunks), content_type,
                                 response.headers.get("ETag"), response.headers.get("Last-Modified"))
    except requests.RequestException as exc:
        raise DescriptionError("network", "Could not retrieve the public posting. Retry later.") from exc
