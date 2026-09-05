"""Compare country-wide and retained city-level collection coverage."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any, Literal


COUNTRYWIDE_SEARCH_LOCATIONS = ("Germany", "Switzerland")
CITY_SEARCH_LOCATIONS = ("Berlin", "Stuttgart", "Frankfurt", "München")

ScopeKey = Literal["countrywide", "cities"]


def _normalize_location(value: object) -> str:
    return (
        unicodedata.normalize("NFD", str(value or "").strip().casefold())
        .encode("ascii", "ignore")
        .decode("ascii")
    )


_LOCATION_SCOPES: dict[str, ScopeKey] = {
    **{_normalize_location(location): "countrywide" for location in COUNTRYWIDE_SEARCH_LOCATIONS},
    **{_normalize_location(location): "cities" for location in CITY_SEARCH_LOCATIONS},
}


def _scope_for_location(location: object) -> ScopeKey | None:
    return _LOCATION_SCOPES.get(_normalize_location(location))


def _as_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def build_collection_scope_comparison(
    query_rows: Iterable[Mapping[str, Any]],
    match_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Aggregate one run's country/city yield, overlap, and request health."""
    metrics: dict[ScopeKey, dict[str, Any]] = {
        "countrywide": {
            "locations": set(),
            "queries": 0,
            "pages_attempted": 0,
            "pages_completed": 0,
            "request_failures": 0,
        },
        "cities": {
            "locations": set(),
            "queries": 0,
            "pages_attempted": 0,
            "pages_completed": 0,
            "request_failures": 0,
        },
    }
    jobs: dict[ScopeKey, set[str]] = {"countrywide": set(), "cities": set()}
    city_jobs: dict[str, set[str]] = {}

    for row in query_rows:
        scope = _scope_for_location(row.get("location"))
        if scope is None:
            continue
        location = _normalize_location(row.get("location"))
        metrics[scope]["locations"].add(location)
        if scope == "cities":
            city_jobs.setdefault(location, set())
        metrics[scope]["queries"] += 1
        for field in ("pages_attempted", "pages_completed", "request_failures"):
            metrics[scope][field] += _as_nonnegative_int(row.get(field))

    if not metrics["countrywide"]["queries"] or not metrics["cities"]["queries"]:
        return None

    for row in match_rows:
        scope = _scope_for_location(row.get("location"))
        job_id = str(row.get("job_id") or "").strip()
        if scope is not None and job_id:
            jobs[scope].add(job_id)
            location = _normalize_location(row.get("location"))
            if location in city_jobs:
                city_jobs[location].add(job_id)

    def finish_scope(scope: ScopeKey, configured_locations: tuple[str, ...]) -> dict[str, Any]:
        attempted = metrics[scope]["pages_attempted"]
        seen_locations = metrics[scope].pop("locations")
        return {
            "locations": [
                location
                for location in configured_locations
                if _normalize_location(location) in seen_locations
            ],
            **metrics[scope],
            "unique_jobs": len(jobs[scope]),
            "page_completion_percent": round(
                metrics[scope]["pages_completed"] / attempted * 100,
                1,
            )
            if attempted
            else 0.0,
        }

    shared_jobs = jobs["countrywide"] & jobs["cities"]
    city_job_count = len(jobs["cities"])
    return {
        "countrywide": finish_scope("countrywide", COUNTRYWIDE_SEARCH_LOCATIONS),
        "cities": finish_scope("cities", CITY_SEARCH_LOCATIONS),
        "shared_jobs": len(shared_jobs),
        "countrywide_only_jobs": len(jobs["countrywide"] - jobs["cities"]),
        "city_only_jobs": len(jobs["cities"] - jobs["countrywide"]),
        "city_jobs_covered_by_countrywide_percent": round(
            len(shared_jobs) / city_job_count * 100,
            1,
        )
        if city_job_count
        else 0.0,
        "per_city": [
            {
                "location": city,
                "unique_jobs": len(city_jobs[_normalize_location(city)]),
                "shared_jobs": len(city_jobs[_normalize_location(city)] & jobs["countrywide"]),
                "city_only_jobs": len(city_jobs[_normalize_location(city)] - jobs["countrywide"]),
            }
            for city in CITY_SEARCH_LOCATIONS if _normalize_location(city) in city_jobs
        ],
    }
