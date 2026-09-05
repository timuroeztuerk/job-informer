"""Read-only evidence for the roadmap's separate-day collection check."""

from __future__ import annotations

from collections import defaultdict
from itertools import product
import sqlite3
from typing import Any

from .collection_scope import (
    CITY_SEARCH_LOCATIONS, COUNTRYWIDE_SEARCH_LOCATIONS,
    build_collection_scope_comparison,
)
from .time_utils import _local_timezone, as_utc_datetime, utc_now


def _terms(value: str | None) -> tuple[str, ...]:
    return tuple(sorted({part.strip().casefold() for part in (value or "").split(",") if part.strip()}))


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cursor = conn.execute(sql)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def build_collection_validation(conn: sqlite3.Connection, *, as_of: Any = None) -> dict[str, Any]:
    """Compare matching requested searches; never certify manual filter quality.

    Use the latest fully specified country/city configuration as the baseline.
    Count the latest healthy attempt on each local start date once, preserving
    failed attempts in the history and keeping different search windows apart.
    """
    zone = _local_timezone()
    reference = as_utc_datetime(as_of, naive_policy="utc") if as_of is not None else utc_now()
    result: dict[str, Any] = {
        "required_days": 3, "healthy_days": 0, "timezone": str(zone), "baseline": None,
        "matching_runs": 0, "other_runs": 0, "runs": [], "cities": [],
    }
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_runs'").fetchone():
        return result
    runs = _rows(conn, """
        SELECT run_id, started_at, status, keywords, locations, time_range
        FROM api_runs WHERE mode = 'run-once' AND COALESCE(trigger, 'manual') = 'manual'
        ORDER BY datetime(started_at) DESC, started_at DESC, run_id
    """)
    runs = [run for run in runs if (date := as_utc_datetime(run["started_at"], naive_policy="utc"))
            and date <= reference]
    required_locations = {location.casefold() for location in (*COUNTRYWIDE_SEARCH_LOCATIONS, *CITY_SEARCH_LOCATIONS)}
    baseline = next((run for run in runs if run["time_range"] and _terms(run["keywords"])
                     and required_locations.issubset(_terms(run["locations"]))), None)
    if baseline is None:
        result["other_runs"] = len(runs)
        return result
    signature = lambda run: (_terms(run["keywords"]), _terms(run["locations"]), run["time_range"])
    key = signature(baseline)
    matching = [run for run in runs if signature(run) == key]
    result.update({
        "baseline": {"keywords": baseline["keywords"], "locations": baseline["locations"],
                     "time_range": baseline["time_range"]},
        "matching_runs": len(matching), "other_runs": len(runs) - len(matching),
    })
    queries: dict[str, list[dict[str, Any]]] = defaultdict(list)
    matches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    run_ids = {run["run_id"] for run in matching}
    for row in _rows(conn, """
        SELECT sr.api_run_id, cq.query_text, cq.location, cq.pages_attempted, cq.pages_completed,
               cq.valid_jobs, cq.request_failures, cq.stop_reason
        FROM collection_queries cq JOIN scrape_runs sr USING (scrape_run_id)
        WHERE sr.api_run_id IS NOT NULL
    """):
        if row["api_run_id"] in run_ids:
            queries[row["api_run_id"]].append(row)
    for row in _rows(conn, """
        SELECT sr.api_run_id, jqm.job_id, cq.query_text, cq.location
        FROM job_query_matches jqm JOIN collection_queries cq USING (collection_query_id)
        JOIN scrape_runs sr ON sr.scrape_run_id = cq.scrape_run_id
        WHERE sr.api_run_id IS NOT NULL
    """):
        if row["api_run_id"] in run_ids:
            matches[row["api_run_id"]].append(row)
    expected = set(product(key[0], key[1]))
    counted_days: set[str] = set()
    cities: dict[str, dict[str, Any]] = {}
    history = []
    for run in matching:
        rows = queries[run["run_id"]]
        actual = {(row["query_text"].strip().casefold(), row["location"].strip().casefold()) for row in rows}
        concerns = []
        if run["status"] != "succeeded":
            concerns.append("Run has not succeeded")
        if actual != expected or len(rows) != len(expected):
            concerns.append("Recorded queries do not match the requested searches")
        if any(row["pages_attempted"] <= 0 or row["pages_completed"] != row["pages_attempted"] for row in rows):
            concerns.append("Missing or incomplete pages")
        if any(row["request_failures"] > 0 for row in rows):
            concerns.append("Request failures")
        if any(row["valid_jobs"] == 0 for row in rows):
            concerns.append("Queries returned no jobs")
        if any(row["stop_reason"] not in {"page_limit", "short_page", "empty_page"} for row in rows):
            concerns.append("Unexpected pagination stop")
        matched_queries = {(row["query_text"].strip().casefold(), row["location"].strip().casefold())
                           for row in matches[run["run_id"]]}
        if matched_queries != expected:
            concerns.append("Job provenance is missing for requested searches")
        comparison = build_collection_scope_comparison(rows, matches[run["run_id"]])
        if comparison is None or not comparison["countrywide"]["unique_jobs"] or not comparison["cities"]["unique_jobs"]:
            concerns.append("Country/city provenance is incomplete")
        date = as_utc_datetime(run["started_at"], naive_policy="utc").astimezone(zone).date().isoformat()
        healthy = not concerns
        counted = healthy and date not in counted_days
        if counted:
            counted_days.add(date)
            for city in comparison["per_city"]:
                total = cities.setdefault(city["location"], {
                    "location": city["location"], "sampled_days": 0, "days_adding_jobs": 0,
                    "unique_jobs": 0, "shared_jobs": 0, "city_only_jobs": 0,
                })
                total["sampled_days"] += 1
                total["days_adding_jobs"] += int(city["city_only_jobs"] > 0)
                for field in ("unique_jobs", "shared_jobs", "city_only_jobs"):
                    total[field] += city[field]
        history.append({
            "run_id": run["run_id"], "started_at": run["started_at"], "date": date,
            "healthy": healthy, "counted": counted, "concerns": concerns,
            "pages_completed": sum(row["pages_completed"] for row in rows),
            "pages_attempted": sum(row["pages_attempted"] for row in rows),
            "city_only_jobs": comparison["city_only_jobs"] if comparison else None,
            "city_coverage_percent": comparison["city_jobs_covered_by_countrywide_percent"] if comparison else None,
        })
    result.update({"healthy_days": len(counted_days), "runs": history,
                   "cities": list(cities.values())})
    return result
