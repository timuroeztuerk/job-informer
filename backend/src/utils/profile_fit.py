"""Profile-fit scoring helpers for ranking jobs against a single-user target profile."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, UTC
from typing import Any, Iterable

from .filtering import normalize_text, should_filter_study_title


DEFAULT_FIT_PROFILE_ID = "default"
DEFAULT_FIT_PROFILE_NAME = "Default Market Profile"
DEFAULT_FIT_PROFILE_VERSION = 1
DEFAULT_FIT_PROFILE_CONFIG: dict[str, Any] = {
    "primary_titles": [
        "data scientist",
        "llm engineer",
        "llm scientist",
        "generative ai engineer",
        "genai engineer",
        "ai engineer",
        "machine learning engineer",
        "machine learning scientist",
        "applied scientist",
    ],
    "secondary_titles": [
        "ai project manager",
        "data project manager",
        "data analyst",
        "analytics engineer",
    ],
    "adjacent_titles": [
        "mlops",
        "mlops engineer",
        "data engineer",
        "analytics engineer",
        "machine learning engineer",
        "platform engineer",
        "backend engineer",
        "software engineer",
        "ai architect",
        "ai platform engineer",
        "data platform engineer",
    ],
    "excluded_title_patterns": [
        "consultant",
        "consulting",
    ],
    "excluded_company_patterns": [
        "ey",
        "ernst & young",
        "ernst young",
        "pwc",
        "pricewaterhousecoopers",
        "deloitte",
        "kpmg",
    ],
    "excluded_seniority": ["lead", "principal"],
    "allowed_regions": [
        "germany",
        "deutschland",
        "austria",
        "osterreich",
        "österreich",
        "switzerland",
        "schweiz",
        "suisse",
        "zurich",
        "zuerich",
        "basel",
        "bern",
        "geneva",
        "genf",
        "vienna",
        "wien",
        "linz",
        "graz",
        "salzburg",
        "berlin",
        "munich",
        "muenchen",
        "hamburg",
        "frankfurt",
        "stuttgart",
        "cologne",
        "koeln",
        "dusseldorf",
        "duesseldorf",
        "leipzig",
        "dresden",
        "hannover",
        "nurnberg",
        "nuremberg",
        "freiburg",
        "augsburg",
        "ulm",
        "konstanz",
    ],
    "language_mode": "ignore",
    "scoring_goal": "broad_market_discovery",
}

LLM_TERMS = [
    "llm",
    "large language model",
    "large language models",
    "generative ai",
    "genai",
    "foundation model",
    "foundation models",
    "rag",
    "retrieval augmented generation",
    "prompt engineering",
    "agentic",
]
DATA_AI_TERMS = [
    "data",
    "machine learning",
    "ml",
    "ai",
    "artificial intelligence",
    "analytics",
    "nlp",
    "llm",
    "genai",
]
PROJECT_MGMT_TERMS = [
    "project manager",
    "program manager",
    "technical project manager",
    "delivery manager",
]
ADJACENT_TECH_TERMS = [
    "data engineer",
    "analytics engineer",
    "ml engineer",
    "machine learning engineer",
    "mlops",
    "mlops",
    "platform engineer",
    "backend engineer",
    "software engineer",
]


def _deep_copy_config(config: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(config))


def default_fit_profile() -> dict[str, Any]:
    """Return the default fit profile record."""
    return {
        "profile_id": DEFAULT_FIT_PROFILE_ID,
        "name": DEFAULT_FIT_PROFILE_NAME,
        "version": DEFAULT_FIT_PROFILE_VERSION,
        "config": _deep_copy_config(DEFAULT_FIT_PROFILE_CONFIG),
    }


def normalize_fit_profile(config: Any) -> dict[str, Any]:
    """Normalize and merge a user profile config with project defaults."""
    normalized = _deep_copy_config(DEFAULT_FIT_PROFILE_CONFIG)

    if config is None:
        return normalized

    payload: dict[str, Any]
    if isinstance(config, str):
        try:
            payload = json.loads(config)
        except json.JSONDecodeError:
            return normalized
    elif isinstance(config, dict):
        payload = config
    else:
        return normalized

    if not isinstance(payload, dict):
        return normalized

    for key, value in payload.items():
        if key not in normalized:
            normalized[key] = value
            continue
        if isinstance(normalized[key], list):
            if isinstance(value, list):
                normalized[key] = [
                    str(item).strip() for item in value if str(item).strip()
                ]
        elif isinstance(normalized[key], str):
            normalized[key] = str(value).strip()
        else:
            normalized[key] = value

    return normalized


def _text_matches_pattern(text: str, pattern: str) -> bool:
    clean_pattern = normalize_text(pattern)
    if not clean_pattern:
        return False
    if " " in clean_pattern:
        return clean_pattern in text
    return re.search(r"\b" + re.escape(clean_pattern) + r"\b", text) is not None


def _matches_any(text: str, patterns: Iterable[str]) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        if _text_matches_pattern(text, pattern):
            matches.append(str(pattern).strip())
    return matches


def _flatten_payload_tokens(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""

    tokens: list[str] = []
    for key in ("summary", "seniority", "employment_type", "remote", "degree_field", "degree_type"):
        value = payload.get(key)
        if value:
            tokens.append(str(value))

    for key in ("languages", "programming_languages", "tools", "skills", "location"):
        value = payload.get(key)
        if isinstance(value, list):
            tokens.extend(str(item) for item in value if str(item).strip())
        elif value:
            tokens.append(str(value))

    salary_range = payload.get("salary_eur_range")
    if isinstance(salary_range, dict):
        if salary_range.get("min") is not None:
            tokens.append(str(salary_range["min"]))
        if salary_range.get("max") is not None:
            tokens.append(str(salary_range["max"]))

    return normalize_text(" ".join(tokens))


def _location_bucket(location_text: str, allowed_regions: list[str]) -> tuple[str, int]:
    if not location_text:
        return "unknown", 0
    if "remote" in location_text:
        if any(token in location_text for token in allowed_regions):
            return "remote_dach", 8
        return "remote_unspecified", 2
    if any(token in location_text for token in allowed_regions):
        return "in_scope", 8
    return "out_of_scope", -10


def _resolve_seniority(job_title_text: str, parsed_payload: dict[str, Any] | None) -> str:
    if parsed_payload:
        seniority = normalize_text(parsed_payload.get("seniority"))
        if seniority:
            return seniority

    if re.search(r"\bprincipal\b", job_title_text):
        return "principal"
    if re.search(r"\blead\b", job_title_text):
        return "lead"
    if re.search(r"\bsenior\b", job_title_text):
        return "senior"
    if re.search(r"\bjunior\b", job_title_text):
        return "junior"
    return ""


def _has_excluded_seniority(
    title_text: str,
    parsed_payload: dict[str, Any] | None,
    excluded_seniority: Iterable[str],
) -> str | None:
    parsed_seniority_text = normalize_text(parsed_payload.get("seniority")) if parsed_payload else ""
    for level in excluded_seniority:
        normalized_level = normalize_text(level)
        if not normalized_level:
            continue
        if _text_matches_pattern(title_text, normalized_level):
            return normalized_level
        if _text_matches_pattern(parsed_seniority_text, normalized_level):
            return normalized_level
    return None


def score_job_fit(
    job: dict[str, Any],
    profile_config: dict[str, Any] | None = None,
    parsed_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a single job against the configured profile."""
    profile = normalize_fit_profile(profile_config)
    title_raw = str(job.get("title", "") or "").strip()
    company_raw = str(job.get("company", "") or "").strip()
    location_raw = str(job.get("location", "") or "").strip()
    description_raw = str(job.get("description", "") or "").strip()

    title_text = normalize_text(title_raw)
    company_text = normalize_text(company_raw)
    location_text = normalize_text(location_raw)
    combined_text = " ".join(
        token
        for token in [
            title_text,
            company_text,
            location_text,
            normalize_text(description_raw),
            _flatten_payload_tokens(parsed_payload),
        ]
        if token
    )

    reasons: list[str] = []
    signals: dict[str, Any] = {
        "role_category": "broad",
        "llm_match": False,
        "location_bucket": "unknown",
        "matched_primary_titles": [],
        "matched_secondary_titles": [],
        "matched_adjacent_titles": [],
        "seniority": _resolve_seniority(title_text, parsed_payload),
        "excluded_reason": None,
    }

    if should_filter_study_title(title_raw):
        signals["excluded_reason"] = "study_role"
        return {
            "score": 0.0,
            "band": "zero",
            "reasons": ["Study-track role"],
            "signals": signals,
        }

    employment_type = normalize_text(parsed_payload.get("employment_type")) if parsed_payload else ""
    if employment_type == "internship":
        signals["excluded_reason"] = "internship"
        return {
            "score": 0.0,
            "band": "zero",
            "reasons": ["Internship role"],
            "signals": signals,
        }

    excluded_companies = [normalize_text(item) for item in profile.get("excluded_company_patterns", [])]
    if _matches_any(company_text, excluded_companies):
        signals["excluded_reason"] = "excluded_company"
        return {
            "score": 0.0,
            "band": "zero",
            "reasons": ["Excluded company"],
            "signals": signals,
        }

    excluded_title_patterns = [normalize_text(item) for item in profile.get("excluded_title_patterns", [])]
    if _matches_any(title_text, excluded_title_patterns) or _matches_any(combined_text, excluded_title_patterns):
        signals["excluded_reason"] = "excluded_role"
        return {
            "score": 0.0,
            "band": "zero",
            "reasons": ["Consulting role"],
            "signals": signals,
        }

    excluded_seniority = [normalize_text(item) for item in profile.get("excluded_seniority", [])]
    matched_excluded_seniority = _has_excluded_seniority(title_text, parsed_payload, excluded_seniority)
    if matched_excluded_seniority:
        signals["excluded_reason"] = "excluded_seniority"
        return {
            "score": 0.0,
            "band": "zero",
            "reasons": [f"Excluded seniority: {matched_excluded_seniority}"],
            "signals": signals,
        }

    score = 12.0

    primary_matches = _matches_any(title_text, profile.get("primary_titles", []))
    secondary_matches = _matches_any(title_text, profile.get("secondary_titles", []))
    adjacent_matches = _matches_any(title_text, profile.get("adjacent_titles", []))
    llm_matches = _matches_any(combined_text, LLM_TERMS)
    data_ai_matches = _matches_any(combined_text, DATA_AI_TERMS)
    project_matches = _matches_any(title_text, PROJECT_MGMT_TERMS)
    adjacent_tech_matches = _matches_any(title_text, ADJACENT_TECH_TERMS)

    if primary_matches:
        score = max(score, 70.0)
        reasons.append("Primary target role")
        signals["role_category"] = "primary"
    elif secondary_matches:
        score = max(score, 54.0)
        reasons.append("Secondary target role")
        signals["role_category"] = "secondary"
    elif project_matches and data_ai_matches:
        score = max(score, 50.0)
        reasons.append("AI/data project role")
        signals["role_category"] = "secondary"
    elif adjacent_matches:
        score = max(score, 42.0)
        reasons.append("Adjacent technical role")
        signals["role_category"] = "adjacent"
    elif adjacent_tech_matches and data_ai_matches:
        score = max(score, 40.0)
        reasons.append("Adjacent technical role with AI/data context")
        signals["role_category"] = "adjacent"
    elif _matches_any(title_text, ["data analyst", "analyst", "analytics"]):
        score = max(score, 34.0)
        reasons.append("Broad analytics/data role")
        signals["role_category"] = "broad"
    elif data_ai_matches:
        score = max(score, 28.0)
        reasons.append("General AI/data relevance")

    if llm_matches:
        score += 18.0
        reasons.append("LLM/GenAI signal")
        signals["llm_match"] = True

    location_bucket, location_delta = _location_bucket(
        location_text,
        [normalize_text(item) for item in profile.get("allowed_regions", [])],
    )
    signals["location_bucket"] = location_bucket
    score += float(location_delta)
    if location_bucket == "in_scope":
        reasons.append("Location in DACH scope")
    elif location_bucket == "out_of_scope":
        reasons.append("Location outside DACH scope")

    if "manager" in title_text and signals["role_category"] == "broad" and not data_ai_matches:
        score = min(score, 24.0)

    if not reasons:
        reasons.append("Broad market-discovery match")

    signals["matched_primary_titles"] = primary_matches
    signals["matched_secondary_titles"] = secondary_matches
    signals["matched_adjacent_titles"] = adjacent_matches or adjacent_tech_matches

    score = max(0.0, min(100.0, score))
    if score >= 75:
        band = "high"
    elif score >= 50:
        band = "medium"
    elif score >= 20:
        band = "low"
    else:
        band = "zero"

    return {
        "score": round(score, 1),
        "band": band,
        "reasons": reasons[:6],
        "signals": signals,
    }


def build_job_fit_rows(
    conn: sqlite3.Connection,
    *,
    profile_id: str = DEFAULT_FIT_PROFILE_ID,
    profile_row: sqlite3.Row | None = None,
    job_ids: Iterable[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build upsert payloads for job fit scores."""
    conn.row_factory = sqlite3.Row
    current_profile_row = profile_row or conn.execute(
        """
        SELECT profile_id, name, config_json, version, created_at, updated_at
        FROM fit_profiles
        WHERE profile_id = ?
        """,
        (profile_id,),
    ).fetchone()
    if current_profile_row is None:
        profile_record = default_fit_profile()
    else:
        profile_record = {
            "profile_id": current_profile_row["profile_id"],
            "name": current_profile_row["name"],
            "version": int(current_profile_row["version"] or DEFAULT_FIT_PROFILE_VERSION),
            "config": normalize_fit_profile(current_profile_row["config_json"]),
            "created_at": current_profile_row["created_at"],
            "updated_at": current_profile_row["updated_at"],
        }

    params: list[Any] = []
    where_parts = ["j.archived_at IS NULL"]
    if job_ids:
        normalized_job_ids = [str(job_id).strip() for job_id in job_ids if str(job_id).strip()]
        if normalized_job_ids:
            placeholders = ",".join("?" for _ in normalized_job_ids)
            where_parts.append(f"j.job_id IN ({placeholders})")
            params.extend(normalized_job_ids)
    where_clause = f"WHERE {' AND '.join(where_parts)}"

    parsed_join = ""
    parsed_select = "NULL AS payload_json"
    if conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='parsed_descriptions'"
    ).fetchone()[0]:
        parsed_select = "parsed.payload_json"
        parsed_join = """
            LEFT JOIN (
                SELECT
                    ranked.job_id,
                    ranked.payload_json
                FROM (
                    SELECT
                        p.job_id,
                        p.payload_json,
                        ROW_NUMBER() OVER (
                            PARTITION BY p.job_id
                            ORDER BY p.version DESC, datetime(p.created_at) DESC, p.rowid DESC
                        ) AS row_num
                    FROM parsed_descriptions p
                ) ranked
                WHERE ranked.row_num = 1
            ) parsed ON parsed.job_id = j.job_id
        """

    rows = conn.execute(
        f"""
        SELECT
            j.job_id,
            j.title,
            j.company,
            j.location,
            j.source,
            j.url,
            j.salary,
            j.description,
            j.scraped_at,
            j.first_seen_at,
            j.last_seen_at,
            j.seen_count,
            {parsed_select}
        FROM jobs j
        {parsed_join}
        {where_clause}
        """,
        params,
    ).fetchall()

    computed_at = datetime.now(UTC).isoformat()
    fit_rows: list[dict[str, Any]] = []
    for row in rows:
        parsed_payload = None
        raw_payload = row["payload_json"] if "payload_json" in row.keys() else None
        if raw_payload:
            try:
                parsed_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                parsed_payload = None

        result = score_job_fit(
            {key: row[key] for key in row.keys() if key != "payload_json"},
            profile_record["config"],
            parsed_payload=parsed_payload,
        )
        fit_rows.append(
            {
                "profile_id": profile_record["profile_id"],
                "job_id": row["job_id"],
                "score": result["score"],
                "band": result["band"],
                "reasons_json": json.dumps(result["reasons"]),
                "signals_json": json.dumps(result["signals"]),
                "computed_at": computed_at,
                "profile_version": profile_record["version"],
            }
        )

    return profile_record, fit_rows
