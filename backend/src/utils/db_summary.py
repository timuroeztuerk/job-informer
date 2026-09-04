"""Small current-state intelligence summary for the local job database."""

from __future__ import annotations

import os
from collections import Counter
from typing import Any

import pandas as pd
from loguru import logger

from .database import JobDatabase
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


def _primary_location(value: object) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return "Unknown"
    first = text.split(",", 1)[0].strip()
    return first or "Unknown"


def _count_rows(counter: Counter[str], total: int, limit: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "count": count,
            "percentage": (count / total * 100) if total else 0.0,
        }
        for name, count in counter.most_common(limit)
    ]


def build_db_summary(db: JobDatabase) -> dict[str, Any]:
    """Return only broad, current-state insights used by the UI."""
    with db._get_connection() as conn:  # noqa: SLF001
        rows = conn.execute(
            """
            SELECT job_id, company, location, source, scraped_at, role_family
            FROM jobs
            WHERE archived_at IS NULL AND LOWER(source) = 'linkedin'
            """
        ).fetchall()
        query_group_rows = conn.execute(
            """
            SELECT DISTINCT jqm.job_id, cq.query_group_key,
                   COALESCE(qg.display_name, cq.query_group_key)
            FROM job_query_matches jqm
            INNER JOIN jobs j ON j.job_id = jqm.job_id
            INNER JOIN collection_queries cq
                ON cq.collection_query_id = jqm.collection_query_id
            LEFT JOIN query_groups qg ON qg.query_group_key = cq.query_group_key
            WHERE j.archived_at IS NULL AND LOWER(j.source) = 'linkedin'
            """
        ).fetchall()
        archived_jobs = int(
            conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE archived_at IS NOT NULL AND LOWER(source) = 'linkedin'"
            ).fetchone()[0]
            or 0
        )
        recent_jobs = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM jobs
                WHERE archived_at IS NULL
                  AND LOWER(source) = 'linkedin'
                  AND datetime(scraped_at) > datetime('now', '-7 days')
                """
            ).fetchone()[0]
            or 0
        )
        date_range = conn.execute(
            """
            SELECT MIN(datetime(scraped_at)), MAX(datetime(scraped_at))
            FROM jobs
            WHERE archived_at IS NULL AND LOWER(source) = 'linkedin'
            """
        ).fetchone()

    total = len(rows)
    companies = Counter(str(row[1] or "Unknown") for row in rows)
    locations = Counter(_primary_location(row[2]) for row in rows)
    sources = Counter(str(row[3] or "Unknown") for row in rows)
    role_families = Counter(str(row[5]) for row in rows if row[5])
    query_groups = Counter(str(row[2]) for row in query_group_rows)
    return {
        "totals": {
            "total_jobs": total,
            "archived_jobs": archived_jobs,
            "recent_jobs_7_days": recent_jobs,
            "date_range": {"earliest": date_range[0], "latest": date_range[1]},
        },
        "jobs_by_source": dict(sources),
        "top_companies": _count_rows(companies, total),
        "top_locations": _count_rows(locations, total),
        "role_families": _count_rows(role_families, total),
        "query_groups": _count_rows(query_groups, total),
        "collection_freshness": compute_collection_freshness(db),
    }
