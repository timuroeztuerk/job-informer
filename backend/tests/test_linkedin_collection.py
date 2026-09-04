"""LinkedIn pagination, retry, and pre-filter persistence tests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from backend.src.agents.job_scraper import JobScraper, LinkedInSource


def _config(*, max_retries: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        user_agent="job-informer-test",
        request_timeout=1,
        max_retries=max_retries,
        request_delay=0,
    )


def _page(start: int, count: int) -> bytes:
    cards = []
    for index in range(start, start + count):
        cards.append(
            f"""
            <div class="job-search-card">
              <h3 class="base-search-card__title">Data Scientist {index}</h3>
              <h4 class="base-search-card__subtitle">Company {index}</h4>
              <span class="job-search-card__location">Berlin</span>
              <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/{1000 + index}/"></a>
            </div>
            """
        )
    return "".join(cards).encode()


class _Response:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content
        self.text = content.decode(errors="ignore")


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def get(self, url: str, **_: object) -> _Response:
        self.urls.append(url)
        return self.responses.pop(0)


class TestLinkedInCollection(unittest.TestCase):
    def test_collects_and_records_more_than_one_page(self) -> None:
        session = _Session([_Response(200, _page(0, 25)), _Response(200, _page(25, 3))])
        source = LinkedInSource(session, _config())

        with patch("backend.src.agents.job_scraper.time.sleep", return_value=None):
            jobs = source.scrape_jobs("Data Scientist", "Berlin", 86400, max_pages=4)

        offsets = [int(parse_qs(urlparse(url).query)["start"][0]) for url in session.urls]
        self.assertEqual(offsets, [0, 25])
        self.assertEqual(len(jobs), 28)
        self.assertEqual(source.last_query_report["pages_attempted"], 2)
        self.assertEqual(source.last_query_report["pages_completed"], 2)
        self.assertEqual(source.last_query_report["page_offsets"], [0, 25])
        self.assertEqual(source.last_query_report["stop_reason"], "short_page")

    def test_stops_when_linkedin_repeats_the_same_page(self) -> None:
        repeated_page = _page(0, 25)
        session = _Session([_Response(200, repeated_page), _Response(200, repeated_page)])
        source = LinkedInSource(session, _config())

        with patch("backend.src.agents.job_scraper.time.sleep", return_value=None):
            jobs = source.scrape_jobs("Data Scientist", "Berlin", 86400, max_pages=4)

        self.assertEqual(len(jobs), 25)
        self.assertEqual(source.last_query_report["stop_reason"], "repeated_page")
        self.assertEqual(source.last_query_report["duplicate_cards"], 25)

    def test_rate_limit_waits_and_retries_the_same_request(self) -> None:
        session = _Session([_Response(429, b"rate limited"), _Response(200, _page(0, 1))])
        source = LinkedInSource(session, _config(max_retries=1))

        def clear_cooldown() -> None:
            source.stats.blocked_until_ts = 0

        with patch.object(source, "_wait_for_cooldown", side_effect=clear_cooldown):
            response = source.request("https://www.linkedin.com/jobs/search/?start=0")

        self.assertIsNotNone(response)
        self.assertEqual(len(session.urls), 2)
        self.assertEqual(session.urls[0], session.urls[1])
        self.assertEqual(source.stats.request_failures, 1)
        self.assertEqual(source.stats.rate_limit_responses, 1)

    def test_candidates_are_not_discarded_before_database_filtering(self) -> None:
        scraper = JobScraper.__new__(JobScraper)
        candidate = {
            "title": "Senior AI Engineer",
            "company": "Example",
            "source": "LinkedIn",
            "url": "https://www.linkedin.com/jobs/view/123456789/",
        }

        observed, fresh, skipped, existing = scraper._collect_observed_jobs(
            [candidate],
            "LinkedIn",
            set(),
        )

        self.assertEqual(observed, [candidate])
        self.assertEqual(fresh, [candidate])
        self.assertEqual(skipped, 0)
        self.assertEqual(existing, 0)


if __name__ == "__main__":
    unittest.main()
