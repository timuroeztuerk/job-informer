"""Canonical identity helpers for LinkedIn job postings."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd


_MISSING_IDENTITY_VALUES = {"", "<na>", "nan", "nat", "none", "null"}


def _clean_identity_value(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    clean = str(value).strip()
    return "" if clean.casefold() in _MISSING_IDENTITY_VALUES else clean


def _parse_identity_url(value: str):
    """Parse absolute URLs and legacy scheme-less host/path values."""
    candidate = value
    if "://" not in candidate and re.match(
        r"^(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]|$)",
        candidate,
        flags=re.IGNORECASE,
    ):
        candidate = f"//{candidate}"
    return urlparse(candidate)


def normalize_job_url(url: Any, source: Any = "") -> str:
    """Return a stable LinkedIn posting identity or a tracking-free URL."""
    value = _clean_identity_value(url)
    if not value:
        return ""

    canonical_match = re.fullmatch(r"linkedin:([^/?#\s]+)", value, re.IGNORECASE)
    if canonical_match:
        return f"linkedin:{canonical_match.group(1)}"

    try:
        parsed = _parse_identity_url(value)
        hostname = (parsed.hostname or "").lower()
        netloc = parsed.netloc.lower()
        path = unquote(parsed.path or "")
        query = parse_qs(parsed.query)

        if hostname == "linkedin.com" or hostname.endswith(".linkedin.com"):
            path_match = re.search(r"/jobs/view/([^/]+)", path, re.IGNORECASE)
            if path_match:
                slug = path_match.group(1).strip()
                id_match = re.fullmatch(r"(\d+)", slug) or re.search(r"-(\d{6,})$", slug)
                if id_match:
                    return f"linkedin:{id_match.group(1)}"

            normalized_query = {key.casefold(): values for key, values in query.items()}
            for job_id in normalized_query.get("currentjobid", []) + normalized_query.get("jobid", []):
                clean_job_id = _clean_identity_value(job_id)
                if clean_job_id.isdigit():
                    return f"linkedin:{clean_job_id}"

        if netloc:
            return f"{netloc}{path}".rstrip("/")
        return value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    except Exception:
        return value


def build_normalized_key(
    *,
    url: Any = "",
    source: Any = "",
    title: Any = "",
    company: Any = "",
) -> str:
    """Build the canonical key used for storage and cross-run deduplication."""
    normalized_url = normalize_job_url(url, source)
    if normalized_url:
        return normalized_url

    fallback_parts = [
        _clean_identity_value(source).casefold(),
        _clean_identity_value(title).casefold(),
        _clean_identity_value(company).casefold(),
    ]
    return "|".join(fallback_parts) if any(fallback_parts) else ""


def build_normalized_keys(df: pd.DataFrame) -> pd.Series:
    """Build canonical keys for a frame of collected jobs."""
    if df.empty:
        return pd.Series(dtype="string")
    keys = df.apply(
        lambda row: build_normalized_key(
            url=row.get("url", ""),
            source=row.get("source", ""),
            title=row.get("title", ""),
            company=row.get("company", ""),
        ),
        axis=1,
    )
    return pd.Series(keys.values, index=df.index)


def build_job_ids(df: pd.DataFrame) -> pd.Series:
    """Use the canonical storage key as each collected job's identifier."""
    return build_normalized_keys(df)
