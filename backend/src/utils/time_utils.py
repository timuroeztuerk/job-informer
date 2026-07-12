"""Canonical timestamp helpers for persisted and API-facing values."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import pandas as pd


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)


def utc_now_iso() -> str:
    """Return the current time in canonical ISO-8601 UTC form."""
    return utc_now().isoformat()


NaiveTimestampPolicy = Literal["local", "utc"]


def as_utc_datetime(value: Any, *, naive_policy: NaiveTimestampPolicy = "local") -> datetime | None:
    """Parse a timestamp and return an aware UTC datetime.

    By default, a naive value is a local wall-clock timestamp. Python's
    ``astimezone`` applies the host timezone (including date-specific DST), then
    converts it to UTC. This matches historical job-market timestamps produced
    by the app's former ``datetime.now()``/``Timestamp.now()`` defaults. Callers
    reading legacy UTC-naive stores (notably API run history) must explicitly use
    ``naive_policy="utc"``. Aware inputs always retain their represented instant.
    """
    if value is None or (isinstance(value, str) and value.strip() in {"", "nan", "NaT"}):
        return None

    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None

    timestamp = pd.Timestamp(parsed)
    python_value = timestamp.to_pydatetime()
    if python_value.tzinfo is None and naive_policy == "utc":
        return python_value.replace(tzinfo=UTC)
    return python_value.astimezone(UTC)


def as_utc_timestamp(value: Any, *, naive_policy: NaiveTimestampPolicy = "local") -> pd.Timestamp:
    """Parse a value using the local-naive compatibility rule for pandas."""
    parsed = as_utc_datetime(value, naive_policy=naive_policy)
    return pd.Timestamp(parsed) if parsed is not None else pd.NaT


def normalize_utc_iso(value: Any, *, naive_policy: NaiveTimestampPolicy = "local") -> str | None:
    """Normalize a timestamp to an aware ``+00:00`` ISO-8601 string."""
    parsed = as_utc_datetime(value, naive_policy=naive_policy)
    return parsed.isoformat() if parsed is not None else None
