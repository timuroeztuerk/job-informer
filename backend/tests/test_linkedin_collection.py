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


def _tracked_page(job_ids: list[int], tracking_id: str) -> bytes:
    cards = []
    for position, job_id in enumerate(job_ids):
        cards.append(
            f"""
            <div class="job-search-card">
              <h3 class="base-search-card__title">Data Scientist {job_id}</h3>
              <h4 class="base-search-card__subtitle">Company {job_id}</h4>
              <span class="job-search-card__location">Berlin</span>
              <a class="base-card__full-link" href="https://de.linkedin.com/jobs/view/{job_id}/?position={position}&amp;trackingId={tracking_id}"></a>
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
    def test_munich_aliases_pin_the_verified_city_on_every_page(self) -> None:
        for location in ["München", "Mu\u0308nchen", "Muenchen", "Munich", " MÜNCHEN ",
                         "Munich, Bavaria, Germany", "München, Deutschland"]:
            with self.subTest(location=location):
                session = _Session([_Response(200, _page(0, 10)), _Response(200, _page(10, 3))])
                source = LinkedInSource(session, _config())
                with patch("backend.src.agents.job_scraper.time.sleep", return_value=None):
                    source.scrape_jobs("Data Scientist", location, 86400, max_pages=4)
                for url in session.urls:
                    query = parse_qs(urlparse(url).query)
                    self.assertEqual(query["geoId"], ["100477049"])
                    self.assertEqual(query["location"], ["Munich, Bavaria, Germany"])
                    self.assertEqual(query["keywords"], ["Data Scientist"])
                    self.assertEqual(query["f_TPR"], ["r86400"])
                    self.assertEqual(query["f_JT"], ["F"])
                self.assertEqual(source.last_query_report["page_offsets"], [0, 10])
                self.assertEqual(source.last_query_report["location"], location)
                self.assertEqual(source.last_query_report["geo_id"], "100477049")
                self.assertEqual(source.last_query_report["search_location"], "Munich, Bavaria, Germany")

    def test_other_locations_are_not_silently_redirected_to_munich(self) -> None:
        source = LinkedInSource(_Session([]), _config())
        for location in ["Germany", "Switzerland", "Berlin", "Frankfurt", "Stuttgart", "Munich, North Dakota, United States"]:
            with self.subTest(location=location):
                query = parse_qs(urlparse(source._build_search_url("Data Analyst", location, 604800, start=20)).query)
                self.assertNotIn("geoId", query)
                self.assertEqual(query["location"], [location])
                self.assertEqual(query["f_TPR"], ["r604800"])
                self.assertEqual(query["start"], ["20"])

    def test_collects_and_records_more_than_one_page(self) -> None:
        session = _Session([_Response(200, _page(0, 10)), _Response(200, _page(10, 3))])
        source = LinkedInSource(session, _config())

        with patch("backend.src.agents.job_scraper.time.sleep", return_value=None):
            jobs = source.scrape_jobs("Data Scientist", "Berlin", 86400, max_pages=4)

        offsets = [int(parse_qs(urlparse(url).query)["start"][0]) for url in session.urls]
        self.assertEqual(offsets, [0, 10])
        self.assertTrue(all("/jobs-guest/jobs/api/seeMoreJobPostings/search" in url for url in session.urls))
        self.assertEqual(len(jobs), 13)
        self.assertEqual(source.last_query_report["pages_attempted"], 2)
        self.assertEqual(source.last_query_report["pages_completed"], 2)
        self.assertEqual(source.last_query_report["page_offsets"], [0, 10])
        self.assertEqual(source.last_query_report["stop_reason"], "short_page")

    def test_stops_when_linkedin_repeats_the_same_page(self) -> None:
        repeated_page = _page(0, 10)
        session = _Session([_Response(200, repeated_page), _Response(200, repeated_page)])
        source = LinkedInSource(session, _config())

        with patch("backend.src.agents.job_scraper.time.sleep", return_value=None):
            jobs = source.scrape_jobs("Data Scientist", "Berlin", 86400, max_pages=4)

        self.assertEqual(len(jobs), 10)
        self.assertEqual(source.last_query_report["stop_reason"], "repeated_page")
        self.assertEqual(source.last_query_report["duplicate_cards"], 10)

    def test_tracking_variants_and_reordered_cards_are_one_repeated_page(self) -> None:
        job_ids = list(range(100000, 100010))
        session = _Session(
            [
                _Response(200, _tracked_page(job_ids, "first-page-token")),
                _Response(200, _tracked_page(list(reversed(job_ids)), "second-page-token")),
            ]
        )
        source = LinkedInSource(session, _config())

        with patch("backend.src.agents.job_scraper.time.sleep", return_value=None):
            jobs = source.scrape_jobs("Data Scientist", "Berlin", 86400, max_pages=4)

        self.assertEqual(len(jobs), 10)
        self.assertEqual(
            jobs[0]["url"],
            "https://www.linkedin.com/jobs/view/100000/",
        )
        self.assertEqual(source.last_query_report["pages_attempted"], 2)
        self.assertEqual(source.last_query_report["stop_reason"], "repeated_page")
        self.assertEqual(source.last_query_report["duplicate_cards"], 10)

    def test_normalizes_linkedin_job_url_before_deduplication(self) -> None:
        self.assertEqual(
            LinkedInSource._normalize_url(
                "https://de.linkedin.com/jobs/view/data-scientist-at-example-4242424242"
                "?position=1&trackingId=changing-token"
            ),
            "https://www.linkedin.com/jobs/view/4242424242/",
        )

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

        observed, existing = scraper._collect_observed_jobs(
            [candidate],
            set(),
        )

        self.assertEqual(observed, [candidate])
        self.assertEqual(existing, 0)


if __name__ == "__main__":
    unittest.main()
