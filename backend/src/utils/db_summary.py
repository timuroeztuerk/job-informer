"""Small current-state intelligence summary for the local job database."""

from __future__ import annotations

import os
import json
from collections import Counter
from typing import Any

import pandas as pd
from loguru import logger

from .database import JobDatabase
from .locations import primary_location
from .relevance_service import AUTO_ARCHIVED_SQL
from .time_utils import as_utc_timestamp


def _as_utc(value: Any | None = None) -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC") if value is None else as_utc_timestamp(value, naive_policy="utc")


def compute_collection_freshness(
    db: JobDatabase,
    *,
    as_of: Any | None = None,
    stale_after_days: float | None = None,
) -> dict[str, Any]:
    """Report the age of the latest real job observation."""
    reference_time = _as_utc(as_of)
    if stale_after_days is None:
        try:
            stale_after_days = float(os.getenv("COLLECTION_STALE_AFTER_DAYS", "7"))
        except ValueError:
            stale_after_days = 7.0
    stale_after_days = max(0.0, stale_after_days)
    fresh_after_days = min(2.0, stale_after_days)
    summary: dict[str, Any] = {
        "as_of": reference_time.isoformat(),
        "last_collected_at": None,
        "latest_observation_at": None,
        "latest_scrape_at": None,
        "source": None,
        "age_days": None,
        "status": "empty",
        "stale_after_days": stale_after_days,
    }

    try:
        with db._get_connection() as conn:  # noqa: SLF001
            observation_values = (
                [
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT o.observed_at
                        FROM job_observations o
                        INNER JOIN jobs j ON j.job_id = o.job_id
                        WHERE LOWER(j.source) = 'linkedin'
                        """
                    ).fetchall()
                ]
                if db._table_exists(conn, "job_observations")  # noqa: SLF001
                else []
            )
            job_values = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT COALESCE(last_seen_at, scraped_at, created_at)
                    FROM jobs
                    WHERE archived_at IS NULL AND LOWER(source) = 'linkedin'
                    """
                ).fetchall()
            ]
            scrape_values = (
                [row[0] for row in conn.execute("SELECT observed_at FROM scrape_runs").fetchall()]
                if db._table_exists(conn, "scrape_runs")  # noqa: SLF001
                else []
            )
    except Exception as exc:  # pragma: no cover - runtime diagnostic
        logger.warning("Could not compute collection freshness: {}", exc)
        return summary

    def latest(values: list[Any]) -> pd.Timestamp | None:
        parsed = [as_utc_timestamp(value) for value in values]
        return max((value for value in parsed if not pd.isna(value)), default=None)

    observation_at = latest(observation_values)
    job_at = latest(job_values)
    scrape_at = latest(scrape_values)
    collected_at = observation_at if observation_at is not None else job_at
    source = "job_observation" if observation_at is not None else "job_record" if job_at is not None else None

    summary["latest_observation_at"] = observation_at.isoformat() if observation_at is not None else None
    summary["latest_scrape_at"] = scrape_at.isoformat() if scrape_at is not None else None
    if collected_at is None:
        return summary

    age_days = max(pd.Timedelta(0), reference_time - collected_at).total_seconds() / 86_400
    summary.update(
        {
            "last_collected_at": collected_at.isoformat(),
            "source": source,
            "age_days": age_days,
            "status": (
                "fresh"
                if age_days <= fresh_after_days
                else "aging"
                if age_days <= stale_after_days
                else "stale"
            ),
        }
    )
    return summary


def _count_rows(counter: Counter[str], total: int, limit: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "key": name,
            "name": name,
            "count": count,
            "percentage": (count / total * 100) if total else 0.0,
        }
        for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0].casefold(), item[0]))[:limit]
    ]


def build_db_summary(db: JobDatabase, *, window: str = "all", as_of: Any | None = None) -> dict[str, Any]:
    """Summarize a reproducible slice of active jobs, keeping sightings distinct."""
    if window not in {"all", "7d", "30d"}:
        raise ValueError("Unknown Intelligence window")
    # Match SQLite datetime() precision so boundary jobs match their drill-down.
    reference = _as_utc(as_of).floor("s")
    recent_since = reference - pd.Timedelta(days=7)
    seen_since = None if window == "all" else (reference - pd.Timedelta(days=int(window[:-1]))).isoformat()
    scope_clause = "LOWER(j.source) = 'linkedin'"
    params: list[str] = []
    if seen_since:
        scope_clause += " AND datetime(COALESCE(j.last_seen_at, j.scraped_at, j.created_at)) >= datetime(?)"
        params.append(seen_since)
    active_clause = scope_clause + " AND j.archived_at IS NULL"
    with db._get_connection() as conn:  # noqa: SLF001
        cursor = conn.execute(
            f"""
            SELECT j.job_id, j.title, COALESCE(NULLIF(j.company, ''), 'Unknown') AS company,
                   j.location, j.postings_json, j.posting_count, j.source, j.url, j.is_favorite,
                   COALESCE(j.scraped_at, j.created_at) AS scraped_at,
                   COALESCE(j.first_seen_at, j.scraped_at, j.created_at) AS first_seen_at,
                   COALESCE(j.last_seen_at, j.scraped_at, j.created_at) AS last_seen_at,
                   COALESCE(j.seen_count, 1) AS seen_count, j.role_family, j.relevance_outcome
            FROM review_jobs j WHERE {active_clause}
            """, params,
        )
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        for row in rows:
            row["postings"] = json.loads(row.pop("postings_json"))
        query_group_rows = conn.execute(
            f"""
            SELECT DISTINCT m.canonical_job_id, cq.query_group_key,
                   COALESCE(qg.display_name, cq.query_group_key)
            FROM job_query_matches jqm
            INNER JOIN job_memberships m ON m.job_id = jqm.job_id
            INNER JOIN review_jobs j ON j.job_id = m.canonical_job_id
            INNER JOIN collection_queries cq
                ON cq.collection_query_id = jqm.collection_query_id
            LEFT JOIN query_groups qg ON qg.query_group_key = cq.query_group_key
            WHERE {active_clause}
            """, params,
        ).fetchall()
        all_active_jobs = int(
            conn.execute(
                "SELECT COUNT(*) FROM review_jobs WHERE archived_at IS NULL AND LOWER(source) = 'linkedin'"
            ).fetchone()[0]
        )
        archived_jobs = int(
            conn.execute(
                f"SELECT COUNT(*) FROM review_jobs j WHERE {scope_clause} AND j.archived_at IS NOT NULL", params,
            ).fetchone()[0]
        )
        automatic_archives = int(conn.execute(
            f"SELECT COUNT(*) FROM review_jobs j WHERE {scope_clause} AND {AUTO_ARCHIVED_SQL}", params,
        ).fetchone()[0])

    total = len(rows)
    companies = Counter(row["company"] for row in rows)
    locations = Counter(location for row in rows for location in {
        primary_location(posting["location"]) for posting in row["postings"]})
    sources = Counter(row["source"] for row in rows)
    role_families = Counter(row["role_family"] or "unclassified" for row in rows)
    query_groups = Counter(str(row[1]) for row in query_group_rows)
    query_names = {str(row[1]): str(row[2]) for row in query_group_rows}
    first_dates = {row["job_id"]: _as_utc(row["scraped_at"]) for row in rows}
    recent = [row for row in rows if first_dates[row["job_id"]] >= recent_since]
    recurring = [row for row in rows if row["seen_count"] > 1]
    recurring.sort(key=lambda row: (
        -row["seen_count"],
        -(_as_utc(row["last_seen_at"]).value if not pd.isna(_as_utc(row["last_seen_at"])) else 0),
        row["job_id"],
    ))
    dates = [date for date in first_dates.values() if not pd.isna(date)]
    company_new = Counter(row["company"] for row in recent)
    company_recurring = Counter(row["company"] for row in recurring)
    return {
        "scope": {"window": window, "as_of": reference.isoformat(), "seen_since": seen_since,
                  "recent_since": recent_since.isoformat(), "all_active_jobs": all_active_jobs},
        "totals": {
            "total_jobs": total,
            "archived_jobs": archived_jobs,
            "recent_jobs_7_days": len(recent),
            "companies": len(companies),
            "locations": len(locations),
            "repeated_jobs": len(recurring),
            "date_range": {"earliest": min(dates).isoformat() if dates else None,
                           "latest": max(dates).isoformat() if dates else None},
        },
        "review": {"unmatched_jobs": sum(row["relevance_outcome"] == "unmatched" for row in rows),
                   "automatic_archives": automatic_archives,
                   "favorite_jobs": sum(bool(row["is_favorite"]) for row in rows)},
        "jobs_by_source": dict(sources),
        "top_companies": [{**item, "new_jobs_7_days": company_new[item["key"]],
                           "repeated_jobs": company_recurring[item["key"]]}
                          for item in _count_rows(companies, total)],
        "top_locations": _count_rows(locations, total),
        "role_families": _count_rows(role_families, total),
        "query_groups": [{**item, "name": query_names[item["key"]]}
                         for item in _count_rows(query_groups, total)],
        "recurring_jobs": [{**row, "is_favorite": bool(row["is_favorite"])} for row in recurring[:5]],
        "collection_freshness": compute_collection_freshness(db, as_of=reference),
    }
