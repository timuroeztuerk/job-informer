"""Helpers to build a structured database summary for the API and CLI."""

from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any, Dict, List

import pandas as pd
from loguru import logger

from .database import JobDatabase
from .time_utils import as_utc_timestamp


CITY_ALIASES = {
    "berlin metropolitan area": "Berlin",
    "berlin area": "Berlin",
    "cologne": "Cologne",
    "koeln": "Cologne",
    "koln": "Cologne",
    "dusseldorf": "Düsseldorf",
    "duesseldorf": "Düsseldorf",
    "frankfurt": "Frankfurt am Main",
    "frankfurt am main": "Frankfurt am Main",
    "munchen": "Munich",
    "muenchen": "Munich",
    "munich": "Munich",
    "remote": "Remote",
    "remote germany": "Remote",
    "remote - germany": "Remote",
    "zurich": "Zurich",
    "zurich area": "Zurich",
    "zuerich": "Zurich",
}

DEGREE_FIELD_ALIASES = {
    "computer science": "computer science",
    "informatics": "computer science",
    "cs": "computer science",
    "data science": "data science",
    "ai": "data science",
    "artificial intelligence": "data science",
    "machine learning": "data science",
    "engineering": "engineering",
    "electrical engineering": "engineering",
    "software engineering": "engineering",
    "statistics": "statistics",
    "mathematics": "mathematics",
    "math": "mathematics",
    "economics": "economics",
    "business administration": "business administration",
    "finance": "finance",
    "related field": "related field",
    "related fields": "related field",
    "bwl": "business administration",
    "wirtschaftsinformatik": "business informatics",
    "datenwissenschaften": "data science",
    "psychology": "psychology",
    "cognitive science": "cognitive science",
    "linguistics": "linguistics",
    "robotics": "robotics",
}


def normalize_city_name(city: str) -> str:
    """Normalize city labels to reduce duplicates caused by casing or accents."""
    if city is None:
        return "Unknown"

    city_clean = " ".join(str(city).strip().split())
    if not city_clean:
        return "Unknown"

    lower_clean = city_clean.lower()
    if lower_clean in {"nan", "none", "null"}:
        return "Unknown"

    ascii_key = (
        pd.Series([city_clean])
        .str.normalize("NFKD")
        .str.encode("ascii", "ignore")
        .str.decode("ascii")
        .iloc[0]
        or city_clean
    )
    ascii_key_lower = ascii_key.lower()

    alias = CITY_ALIASES.get(ascii_key_lower) or CITY_ALIASES.get(lower_clean)
    if alias:
        return alias

    if ascii_key_lower.startswith("remote") or lower_clean.startswith("remote"):
        return "Remote"

    return city_clean.title()


def extract_primary_city(location: str) -> str:
    """Return normalized primary city extracted from a full location string."""
    if location is None:
        return "Unknown"

    primary = str(location).split(",")[0]
    return normalize_city_name(primary)


def normalize_degree_fields(value: Any) -> list[str]:
    """Return a list of normalized degree fields derived from an LLM payload value."""
    if value is None:
        return ["unspecified"]

    if isinstance(value, list):
        candidates = value
    else:
        candidates = pd.Series([str(value)]).str.split(r"[|/,]").iloc[0]

    normalized: list[str] = []
    for candidate in candidates:
        clean = " ".join(str(candidate).strip().split()).lower()
        if not clean or clean in {"", "null", "none"}:
            continue
        if clean in {"unspecified", "not specified"}:
            normalized.append("unspecified")
            continue
        alias = DEGREE_FIELD_ALIASES.get(clean, clean)
        normalized.append(alias)

    if not normalized:
        return ["unspecified"]

    seen = set()
    unique = []
    for item in normalized:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _sorted_counter(counter: Dict[str, int], total: int, limit: int | None = None) -> List[Dict[str, Any]]:
    items = sorted(counter.items(), key=lambda x: x[1], reverse=True)
    if limit:
        items = items[:limit]
    return [
        {"name": name, "count": count, "percentage": (count / total) * 100 if total else 0.0}
        for name, count in items
    ]


def _safe_avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _normalize_token(value: Any) -> str:
    clean = " ".join(str(value).strip().split())
    if not clean:
        return ""
    lower_clean = clean.lower()
    if lower_clean in {"unspecified", "none", "null", "nan"}:
        return ""
    return lower_clean


def _increment_counter(counter: Counter[str], values: Any) -> None:
    if values is None:
        return

    if isinstance(values, list):
        candidates = values
    else:
        candidates = [values]

    for candidate in candidates:
        token = _normalize_token(candidate)
        if token:
            counter[token] += 1


def _load_jobs_with_dates(db: JobDatabase) -> pd.DataFrame:
    try:
        with db._get_connection() as conn:  # noqa: SLF001
            jobs_df = pd.read_sql_query(
                """
                SELECT
                    job_id,
                    title,
                    company,
                    location,
                    source,
                    COALESCE(scraped_at, created_at) AS observed_at
                FROM jobs
                WHERE archived_at IS NULL
                """,
                conn,
            )
    except Exception as exc:  # pragma: no cover - defensive for runtime diagnostics
        logger.warning(f"Could not load jobs for summary: {exc}")
        return pd.DataFrame()

    if jobs_df.empty:
        return jobs_df

    jobs_df["observed_at"] = jobs_df["observed_at"].apply(as_utc_timestamp)
    jobs_df = jobs_df.dropna(subset=["observed_at"]).copy()
    return jobs_df


def _load_latest_parsed_rows(db: JobDatabase) -> pd.DataFrame:
    try:
        with db._get_connection() as conn:  # noqa: SLF001
            if not db._table_exists(conn, "parsed_descriptions"):  # noqa: SLF001
                return pd.DataFrame()
            parsed_df = pd.read_sql_query(
                """
                SELECT
                    ranked.job_id,
                    ranked.payload_json,
                    ranked.version,
                    ranked.created_at,
                    ranked.company,
                    ranked.location,
                    ranked.source,
                    ranked.observed_at
                FROM (
                    SELECT
                        p.job_id,
                        p.payload_json,
                        p.version,
                        p.created_at,
                        j.company,
                        j.location,
                        j.source,
                        COALESCE(j.scraped_at, j.created_at) AS observed_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY p.job_id
                            ORDER BY
                                p.version DESC,
                                datetime(p.created_at) DESC,
                                p.rowid DESC
                        ) AS row_num
                    FROM parsed_descriptions p
                    INNER JOIN jobs j ON j.job_id = p.job_id
                    WHERE j.archived_at IS NULL
                ) ranked
                WHERE ranked.row_num = 1
                """,
                conn,
            )
    except Exception as exc:  # pragma: no cover - defensive for runtime diagnostics
        logger.warning(f"Could not load parsed descriptions for summary: {exc}")
        return pd.DataFrame()

    if parsed_df.empty:
        return parsed_df

    parsed_df["observed_at"] = parsed_df["observed_at"].apply(as_utc_timestamp)
    return parsed_df.dropna(subset=["observed_at"]).copy()


def _week_start(series: pd.Series) -> pd.Series:
    normalized = series.apply(as_utc_timestamp).dt.normalize()
    return normalized - pd.to_timedelta(normalized.dt.weekday, unit="D")


def _as_utc_timestamp(value: Any | None = None) -> pd.Timestamp:
    """Return a timezone-aware UTC timestamp for stable date comparisons."""
    # Explicit analytical boundaries are UTC by contract; only persisted legacy
    # market timestamps use the local-wall compatibility policy.
    return pd.Timestamp.now(tz="UTC") if value is None else as_utc_timestamp(value, naive_policy="utc")


def compute_collection_freshness(
    db: JobDatabase,
    *,
    as_of: Any | None = None,
    stale_after_days: float | None = None,
) -> dict[str, Any]:
    """Describe how old the latest actual collected observation is.

    Fresh means no more than two days old, aging lasts until the configurable
    stale threshold, and anything older is stale. Scrape-run time is reported separately so
    an empty recent run cannot make an old collection look current.
    """
    reference_time = _as_utc_timestamp(as_of)
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

    latest_observation = None
    latest_job_record = None
    latest_scrape = None
    try:
        with db._get_connection() as conn:  # noqa: SLF001
            if db._table_exists(conn, "job_observations"):  # noqa: SLF001
                row = conn.execute(
                    """
                    SELECT observed_at
                    FROM job_observations
                    ORDER BY datetime(observed_at, 'utc') DESC, observation_id DESC
                    LIMIT 1
                    """
                ).fetchone()
                latest_observation = row[0] if row else None

            row = conn.execute(
                """
                SELECT COALESCE(last_seen_at, scraped_at, created_at)
                FROM jobs
                WHERE archived_at IS NULL
                ORDER BY datetime(COALESCE(last_seen_at, scraped_at, created_at), 'utc') DESC
                LIMIT 1
                """
            ).fetchone()
            latest_job_record = row[0] if row else None

            if db._table_exists(conn, "scrape_runs"):  # noqa: SLF001
                row = conn.execute(
                    """
                    SELECT observed_at
                    FROM scrape_runs
                    ORDER BY datetime(observed_at, 'utc') DESC
                    LIMIT 1
                    """
                ).fetchone()
                latest_scrape = row[0] if row else None
    except Exception as exc:  # pragma: no cover - defensive for runtime diagnostics
        logger.warning(f"Could not compute collection freshness: {exc}")
        return summary

    observation_timestamp = as_utc_timestamp(latest_observation) if latest_observation else pd.NaT
    job_timestamp = as_utc_timestamp(latest_job_record) if latest_job_record else pd.NaT
    scrape_timestamp = as_utc_timestamp(latest_scrape) if latest_scrape else pd.NaT

    if not pd.isna(observation_timestamp):
        collected_at = observation_timestamp
        source = "job_observation"
    elif not pd.isna(job_timestamp):
        collected_at = job_timestamp
        source = "job_record"
    else:
        collected_at = None
        source = None

    summary["latest_observation_at"] = (
        observation_timestamp.isoformat() if not pd.isna(observation_timestamp) else None
    )
    summary["latest_scrape_at"] = scrape_timestamp.isoformat() if not pd.isna(scrape_timestamp) else None
    if collected_at is None:
        return summary

    age = max(pd.Timedelta(0), reference_time - collected_at)
    age_days = age.total_seconds() / 86_400
    status = "fresh" if age_days <= fresh_after_days else "aging" if age_days <= stale_after_days else "stale"
    summary.update(
        {
            "last_collected_at": collected_at.isoformat(),
            "source": source,
            "age_days": age_days,
            "status": status,
        }
    )
    return summary


def compute_skill_gap_summary(db: JobDatabase, *, limit: int = 8) -> dict[str, Any]:
    """Aggregate normalized personal skill gaps across active annotated jobs."""
    summary: dict[str, Any] = {"jobs_with_gaps": 0, "top_gaps": []}
    try:
        with db._get_connection() as conn:  # noqa: SLF001
            rows = conn.execute(
                """
                SELECT a.skill_gaps
                FROM job_annotations a
                INNER JOIN jobs j ON j.job_id = a.job_id
                WHERE j.archived_at IS NULL
                  AND a.skill_gaps IS NOT NULL
                  AND TRIM(a.skill_gaps) NOT IN ('', '[]')
                """
            ).fetchall()
    except Exception as exc:  # pragma: no cover - defensive for runtime diagnostics
        logger.warning(f"Could not compute annotation skill gaps: {exc}")
        return summary

    gap_counts: Counter[str] = Counter()
    jobs_with_gaps = 0
    for row in rows:
        raw_gaps = row[0]
        try:
            decoded = json.loads(raw_gaps)
        except (json.JSONDecodeError, TypeError):
            decoded = [raw_gaps]
        candidates = decoded if isinstance(decoded, list) else [decoded]
        normalized = {_normalize_token(candidate) for candidate in candidates}
        normalized.discard("")
        if not normalized:
            continue
        jobs_with_gaps += 1
        gap_counts.update(normalized)

    summary["jobs_with_gaps"] = jobs_with_gaps
    summary["top_gaps"] = _sorted_counter(gap_counts, jobs_with_gaps, limit=max(1, int(limit)))
    return summary


def _rank_momentum(counter_recent: Counter[str], counter_previous: Counter[str], *, limit: int = 5) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for name in set(counter_recent) | set(counter_previous):
        recent_count = int(counter_recent.get(name, 0))
        previous_count = int(counter_previous.get(name, 0))
        delta = recent_count - previous_count
        if recent_count <= 0 or delta <= 0:
            continue
        ranked.append(
            {
                "name": name,
                "recent_count": recent_count,
                "previous_count": previous_count,
                "delta": delta,
            }
        )

    ranked.sort(key=lambda item: (item["delta"], item["recent_count"], item["name"]), reverse=True)
    return ranked[:limit]


SENIOR_LABELS = {"senior", "lead", "principal"}


def compute_parsed_insights(db: JobDatabase, *, total_jobs: int | None = None, parsed_stats: dict | None = None) -> dict:
    """Summarize parsed description payloads (latest version per current job)."""
    insights: dict = {
        "total_records": 0,
        "coverage_pct": None,
        "orphaned_jobs": 0,
        "historical_payloads": None,
        "seniority_mix": {
            "senior": {"count": 0, "percentage": 0.0, "avg_experience": None, "avg_salary": None},
            "non_senior": {"count": 0, "percentage": 0.0, "avg_experience": None, "avg_salary": None},
        },
        "programming_languages": [],
        "skills": [],
        "tools": [],
        "seniority_levels": [],
        "employment_types": [],
        "remote_options": [],
        "languages": [],
        "degree_fields": [],
        "degree_types": [],
        "experience_years": {"count": 0, "average": None, "min": None, "max": None},
        "salary_eur": {"count": 0, "average": None, "min": None, "max": None},
    }

    parsed_df = _load_latest_parsed_rows(db)
    if parsed_df.empty:
        return insights

    programming_languages: Counter[str] = Counter()
    skills: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    seniority_levels: Counter[str] = Counter()
    employment_types: Counter[str] = Counter()
    remote_options: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    degree_fields: Counter[str] = Counter()
    degree_types: Counter[str] = Counter()
    experience_years: list[int] = []
    salary_ranges: list[float] = []
    seniority_groups = {
        "senior": {"count": 0, "experience": [], "salary": []},
        "non_senior": {"count": 0, "experience": [], "salary": []},
    }

    for _, row in parsed_df.iterrows():
        try:
            data = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue

        _increment_counter(programming_languages, data.get("programming_languages"))
        _increment_counter(skills, data.get("skills"))
        _increment_counter(tools, data.get("tools"))

        seniority = data.get("seniority")
        if seniority and seniority != "unspecified":
            seniority_levels[str(seniority)] += 1
        # Bucket for senior vs other roles (including unspecified as non_senior to keep ratios simple)
        group = "senior" if str(seniority).lower() in SENIOR_LABELS else "non_senior"
        seniority_groups[group]["count"] += 1

        employment_type = data.get("employment_type")
        if employment_type and employment_type != "unspecified":
            employment_types[str(employment_type)] += 1

        remote_value = data.get("remote")
        if remote_value and remote_value != "unspecified":
            remote_options[str(remote_value)] += 1

        _increment_counter(languages, data.get("languages"))

        for field in normalize_degree_fields(data.get("degree_field")):
            if field:
                degree_fields[str(field)] += 1

        degree_type = data.get("degree_type")
        if degree_type and degree_type != "unspecified":
            degree_types[str(degree_type)] += 1

        years_min = data.get("years_experience_min")
        if years_min is not None:
            try:
                years_val = int(years_min)
                if 0 <= years_val <= 20:
                    experience_years.append(years_val)
                    seniority_groups[group]["experience"].append(years_val)
            except (ValueError, TypeError):
                pass

        salary_range = data.get("salary_eur_range") or {}
        salary_min = salary_range.get("min")
        salary_max = salary_range.get("max")
        salary_avg = None
        if salary_min is not None and salary_max is not None:
            try:
                salary_avg = (float(salary_min) + float(salary_max)) / 2
                if 20000 <= salary_avg <= 200000:
                    salary_ranges.append(salary_avg)
            except (ValueError, TypeError):
                pass
        if salary_avg is not None:
            seniority_groups[group]["salary"].append(salary_avg)

    total_records = len(parsed_df)
    insights["total_records"] = total_records
    if total_jobs:
        insights["coverage_pct"] = (total_records / total_jobs) * 100 if total_jobs else 0.0

    if parsed_stats:
        insights["orphaned_jobs"] = int(parsed_stats.get("orphaned_parsed_descriptions", 0) or 0)
        insights["historical_payloads"] = int(parsed_stats.get("total_parsed_descriptions", total_records) or total_records)
    else:
        insights["orphaned_jobs"] = 0
        insights["historical_payloads"] = total_records
    insights["programming_languages"] = _sorted_counter(programming_languages, total_records, limit=8)
    insights["skills"] = _sorted_counter(skills, total_records, limit=8)
    insights["tools"] = _sorted_counter(tools, total_records, limit=10)
    insights["seniority_levels"] = _sorted_counter(seniority_levels, total_records, limit=5)
    insights["employment_types"] = _sorted_counter(employment_types, total_records, limit=5)
    insights["remote_options"] = _sorted_counter(remote_options, total_records, limit=5)
    insights["languages"] = _sorted_counter(languages, total_records, limit=5)

    sorted_fields = sorted(degree_fields.items(), key=lambda x: x[1], reverse=True)
    filtered_fields = [(field, count) for field, count in sorted_fields if field != "unspecified"]
    degree_field_items = filtered_fields or sorted_fields
    insights["degree_fields"] = [
        {"name": name, "count": count, "percentage": (count / total_records) * 100 if total_records else 0.0}
        for name, count in degree_field_items[:6]
    ]
    insights["degree_types"] = _sorted_counter(degree_types, total_records, limit=5)

    if experience_years:
        insights["experience_years"] = {
            "count": len(experience_years),
            "average": _safe_avg(experience_years),
            "min": min(experience_years),
            "max": max(experience_years),
        }

    if salary_ranges:
        insights["salary_eur"] = {
            "count": len(salary_ranges),
            "average": _safe_avg(salary_ranges),
            "min": min(salary_ranges),
            "max": max(salary_ranges),
        }

    # Senior vs non-senior comparison
    if total_records:
        for group_name, payload in seniority_groups.items():
            pct = (payload["count"] / total_records) * 100 if total_records else 0.0
            insights["seniority_mix"][group_name] = {
                "count": payload["count"],
                "percentage": pct,
                "avg_experience": _safe_avg(payload["experience"]),
                "avg_salary": _safe_avg(payload["salary"]),
            }
    else:
        for group_name in seniority_groups:
            insights["seniority_mix"][group_name] = {
                "count": 0,
                "percentage": 0.0,
                "avg_experience": None,
                "avg_salary": None,
            }

    return insights


def compute_city_summary(db: JobDatabase) -> dict:
    """Summarize city distribution across jobs."""
    summary = {"top_cities": [], "total_jobs": 0}
    try:
        with db._get_connection() as conn:  # noqa: SLF001
            df = pd.read_sql_query("SELECT location FROM jobs WHERE archived_at IS NULL", conn)
    except Exception as exc:  # pragma: no cover - defensive for runtime diagnostics
        logger.warning(f"Could not load city data for summary: {exc}")
        return summary

    if df is None or df.empty:
        return summary

    city_series = df["location"].apply(extract_primary_city)
    city_counts = city_series.value_counts()
    total_entries = len(city_series)

    summary["total_jobs"] = int(total_entries)
    summary["top_cities"] = [
        {"name": city, "count": int(count), "percentage": (count / total_entries) * 100 if total_entries else 0.0}
        for city, count in city_counts.head(5).items()
    ]
    return summary


def compute_observation_summary(db: JobDatabase) -> dict:
    """Summarize repeated sightings and posting longevity."""
    summary: dict[str, Any] = {
        "total_observations": 0,
        "repeat_jobs": 0,
        "average_seen_count": None,
        "max_seen_count": 0,
        "top_recurring_jobs": [],
    }

    try:
        with db._get_connection() as conn:  # noqa: SLF001
            jobs_df = pd.read_sql_query(
                """
                SELECT
                    job_id,
                    title,
                    company,
                    first_seen_at,
                    last_seen_at,
                    COALESCE(seen_count, 1) AS seen_count
                FROM jobs
                WHERE archived_at IS NULL
                """,
                conn,
            )
    except Exception as exc:  # pragma: no cover - defensive for runtime diagnostics
        logger.warning(f"Could not load observation data for summary: {exc}")
        return summary

    if jobs_df.empty:
        return summary

    jobs_df["first_seen_at"] = jobs_df["first_seen_at"].apply(as_utc_timestamp)
    jobs_df["last_seen_at"] = jobs_df["last_seen_at"].apply(as_utc_timestamp)
    jobs_df["seen_count"] = pd.to_numeric(jobs_df["seen_count"], errors="coerce").fillna(1).clip(lower=1)
    jobs_df["active_days"] = (
        (jobs_df["last_seen_at"] - jobs_df["first_seen_at"]).dt.days.fillna(0).clip(lower=0) + 1
    )

    seen_counts = jobs_df["seen_count"]
    summary["repeat_jobs"] = int((seen_counts > 1).sum())
    summary["average_seen_count"] = float(seen_counts.mean()) if not seen_counts.empty else None
    summary["max_seen_count"] = int(seen_counts.max()) if not seen_counts.empty else 0

    try:
        with db._get_connection() as conn:  # noqa: SLF001
            row = conn.execute("SELECT COUNT(*) FROM job_observations").fetchone()
            summary["total_observations"] = int(row[0]) if row else 0
    except Exception as exc:  # pragma: no cover - defensive for runtime diagnostics
        logger.warning(f"Could not count job observations for summary: {exc}")

    recurring = jobs_df.sort_values(
        by=["seen_count", "active_days", "last_seen_at"],
        ascending=[False, False, False],
    ).head(5)
    summary["top_recurring_jobs"] = [
        {
            "job_id": str(row["job_id"]),
            "title": str(row["title"]),
            "company": str(row["company"]),
            "seen_count": int(row["seen_count"]),
            "active_days": int(row["active_days"]),
            "first_seen_at": row["first_seen_at"].isoformat() if not pd.isna(row["first_seen_at"]) else None,
            "last_seen_at": row["last_seen_at"].isoformat() if not pd.isna(row["last_seen_at"]) else None,
        }
        for _, row in recurring.iterrows()
    ]
    return summary


def compute_trend_summary(
    db: JobDatabase,
    *,
    window_days: int = 30,
    week_buckets: int = 12,
    as_of: Any | None = None,
) -> dict:
    """Summarize market momentum in windows anchored to current UTC."""
    reference_time = _as_utc_timestamp(as_of)
    recent_start = reference_time - pd.Timedelta(days=window_days)
    previous_start = recent_start - pd.Timedelta(days=window_days)
    summary: dict[str, Any] = {
        "window_days": window_days,
        "recent_window": {
            "start": recent_start.isoformat(),
            "end": reference_time.isoformat(),
            "jobs": 0,
        },
        "previous_window": {
            "start": previous_start.isoformat(),
            "end": recent_start.isoformat(),
            "jobs": 0,
        },
        "weekly_job_counts": [],
        "momentum": {
            "skills": [],
            "tools": [],
            "programming_languages": [],
            "companies": [],
            "cities": [],
        },
    }

    jobs_df = _load_jobs_with_dates(db)
    if jobs_df.empty:
        return summary

    parsed_df = _load_latest_parsed_rows(db)

    recent_jobs = jobs_df.loc[
        (jobs_df["observed_at"] >= recent_start) & (jobs_df["observed_at"] <= reference_time)
    ].copy()
    previous_jobs = jobs_df.loc[
        (jobs_df["observed_at"] >= previous_start) & (jobs_df["observed_at"] < recent_start)
    ].copy()

    summary["recent_window"] = {
        "start": recent_start.isoformat(),
        "end": reference_time.isoformat(),
        "jobs": int(recent_jobs["job_id"].nunique()),
    }
    summary["previous_window"] = {
        "start": previous_start.isoformat(),
        "end": recent_start.isoformat(),
        "jobs": int(previous_jobs["job_id"].nunique()),
    }

    current_week_start = _week_start(pd.Series([reference_time])).iloc[0]
    week_index = [current_week_start - pd.Timedelta(weeks=offset) for offset in range(week_buckets - 1, -1, -1)]

    jobs_weekly = jobs_df.assign(week_start=_week_start(jobs_df["observed_at"]))
    jobs_by_week = jobs_weekly.groupby("week_start")["job_id"].nunique().to_dict()

    parsed_by_week: dict[pd.Timestamp, int] = {}
    if not parsed_df.empty:
        parsed_weekly = parsed_df.assign(week_start=_week_start(parsed_df["observed_at"]))
        parsed_by_week = parsed_weekly.groupby("week_start")["job_id"].nunique().to_dict()

    summary["weekly_job_counts"] = [
        {
            "week_start": week_start.isoformat(),
            "jobs": int(jobs_by_week.get(week_start, 0)),
            "parsed_jobs": int(parsed_by_week.get(week_start, 0)),
        }
        for week_start in week_index
    ]

    company_recent = Counter(_normalize_token(value) for value in recent_jobs["company"] if _normalize_token(value))
    company_previous = Counter(
        _normalize_token(value) for value in previous_jobs["company"] if _normalize_token(value)
    )
    city_recent = Counter(extract_primary_city(value) for value in recent_jobs["location"] if value)
    city_previous = Counter(extract_primary_city(value) for value in previous_jobs["location"] if value)

    summary["momentum"]["companies"] = _rank_momentum(company_recent, company_previous)
    summary["momentum"]["cities"] = _rank_momentum(city_recent, city_previous)

    if parsed_df.empty:
        return summary

    recent_parsed = parsed_df.loc[
        (parsed_df["observed_at"] >= recent_start) & (parsed_df["observed_at"] <= reference_time)
    ]
    previous_parsed = parsed_df.loc[
        (parsed_df["observed_at"] >= previous_start) & (parsed_df["observed_at"] < recent_start)
    ]

    skill_recent: Counter[str] = Counter()
    skill_previous: Counter[str] = Counter()
    tool_recent: Counter[str] = Counter()
    tool_previous: Counter[str] = Counter()
    language_recent: Counter[str] = Counter()
    language_previous: Counter[str] = Counter()

    for _, row in recent_parsed.iterrows():
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        _increment_counter(skill_recent, payload.get("skills"))
        _increment_counter(tool_recent, payload.get("tools"))
        _increment_counter(language_recent, payload.get("programming_languages"))

    for _, row in previous_parsed.iterrows():
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        _increment_counter(skill_previous, payload.get("skills"))
        _increment_counter(tool_previous, payload.get("tools"))
        _increment_counter(language_previous, payload.get("programming_languages"))

    summary["momentum"]["skills"] = _rank_momentum(skill_recent, skill_previous)
    summary["momentum"]["tools"] = _rank_momentum(tool_recent, tool_previous)
    summary["momentum"]["programming_languages"] = _rank_momentum(language_recent, language_previous)
    return summary


def build_db_summary(db: JobDatabase) -> dict:
    """Assemble a structured DB summary for API/CLI consumers."""
    base = db.get_job_summary()
    parsed_stats = base.get("parsed_descriptions_stats") or {}

    return {
        "totals": {
            "total_jobs": base.get("total_jobs", 0),
            "recent_jobs_7_days": base.get("recent_jobs_7_days", 0),
            "date_range": base.get("date_range") or {"earliest": None, "latest": None},
        },
        "jobs_by_source": base.get("jobs_by_source", {}),
        "top_companies": base.get("top_companies", {}),
        "observation_stats": base.get("observation_stats", {}),
        "observation_summary": compute_observation_summary(db),
        "collection_freshness": compute_collection_freshness(db),
        "skill_gap_summary": compute_skill_gap_summary(db),
        "profile_fit_summary": db.get_fit_summary(),
        "parser_telemetry": db.get_parser_telemetry_summary(),
        "integrity": db.get_integrity_report(sample_limit=5),
        "parsed_descriptions_stats": base.get("parsed_descriptions_stats", {}),
        "parsed_insights": compute_parsed_insights(
            db,
            total_jobs=base.get("total_jobs"),
            parsed_stats=parsed_stats,
        ),
        "city_summary": compute_city_summary(db),
        "trend_summary": compute_trend_summary(db),
    }
