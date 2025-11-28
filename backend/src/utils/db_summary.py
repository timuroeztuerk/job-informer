"""Helpers to build a structured database summary for the API and CLI."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List

import pandas as pd
from loguru import logger

from .database import JobDatabase


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


def compute_parsed_insights(db: JobDatabase) -> dict:
    """Summarize parsed description payloads (latest version per job)."""
    insights: dict = {
        "total_records": 0,
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

    try:
        with db._get_connection() as conn:  # noqa: SLF001
            parsed_df = pd.read_sql_query(
                """
                SELECT p.job_id, p.payload_json, p.version
                FROM parsed_descriptions p
                INNER JOIN (
                    SELECT job_id, MAX(version) as max_version
                    FROM parsed_descriptions
                    GROUP BY job_id
                ) latest ON p.job_id = latest.job_id AND p.version = latest.max_version
                """,
                conn,
            )
    except Exception as exc:  # pragma: no cover - defensive for runtime diagnostics
        logger.warning(f"Could not load parsed descriptions for summary: {exc}")
        return insights

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

    for _, row in parsed_df.iterrows():
        try:
            data = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue

        for lang in data.get("programming_languages") or []:
            if lang and str(lang).strip():
                programming_languages[str(lang)] += 1

        for skill in data.get("skills") or []:
            if skill and str(skill).strip():
                skills[str(skill)] += 1

        for tool in data.get("tools") or []:
            if tool and str(tool).strip():
                tools[str(tool)] += 1

        seniority = data.get("seniority")
        if seniority and seniority != "unspecified":
            seniority_levels[str(seniority)] += 1

        employment_type = data.get("employment_type")
        if employment_type and employment_type != "unspecified":
            employment_types[str(employment_type)] += 1

        remote_value = data.get("remote")
        if remote_value and remote_value != "unspecified":
            remote_options[str(remote_value)] += 1

        for lang in data.get("languages") or []:
            if lang and str(lang).strip():
                languages[str(lang)] += 1

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
            except (ValueError, TypeError):
                pass

        salary_range = data.get("salary_eur_range") or {}
        salary_min = salary_range.get("min")
        salary_max = salary_range.get("max")
        if salary_min is not None and salary_max is not None:
            try:
                avg_salary = (float(salary_min) + float(salary_max)) / 2
                if 20000 <= avg_salary <= 200000:
                    salary_ranges.append(avg_salary)
            except (ValueError, TypeError):
                pass

    total_records = len(parsed_df)
    insights["total_records"] = total_records
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

    return insights


def compute_city_summary(db: JobDatabase) -> dict:
    """Summarize city distribution across jobs."""
    summary = {"top_cities": [], "total_jobs": 0}
    try:
        with db._get_connection() as conn:  # noqa: SLF001
            df = pd.read_sql_query("SELECT location FROM jobs", conn)
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


def build_db_summary(db: JobDatabase) -> dict:
    """Assemble a structured DB summary for API/CLI consumers."""
    base = db.get_job_summary()

    return {
        "totals": {
            "total_jobs": base.get("total_jobs", 0),
            "recent_jobs_7_days": base.get("recent_jobs_7_days", 0),
            "date_range": base.get("date_range") or {"earliest": None, "latest": None},
        },
        "jobs_by_source": base.get("jobs_by_source", {}),
        "top_companies": base.get("top_companies", {}),
        "parsed_descriptions_stats": base.get("parsed_descriptions_stats", {}),
        "parsed_insights": compute_parsed_insights(db),
        "city_summary": compute_city_summary(db),
    }
