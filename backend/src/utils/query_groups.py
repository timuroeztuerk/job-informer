"""Small, deterministic mapping from LinkedIn terms to search intentions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .filtering import normalize_text


@dataclass(frozen=True)
class QuerySpec:
    group_key: str
    display_name: str
    query_text: str


_KNOWN_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("data_science", "Data science", (r"\bdata scientist\b", r"\bdecision scientist\b")),
    (
        "analytics_bi",
        "Analytics and BI",
        (r"\bdata analyst\b", r"\banalytics?\b", r"\bbusiness intelligence\b", r"\bbi analyst\b"),
    ),
    (
        "data_engineering",
        "Data engineering",
        (r"\bdata engineer\b", r"\bdata platform\b", r"\bdata architect\b", r"\bdata warehouse\b"),
    ),
    (
        "ml_engineering",
        "ML engineering",
        (r"\bmachine learning\b", r"\bmlops\b", r"\bml engineer\b"),
    ),
)


def query_spec_for_term(query_text: str) -> QuerySpec:
    """Assign a stable group to a configured query without hiding custom terms."""
    cleaned = " ".join(str(query_text or "").split()).strip()
    normalized = normalize_text(cleaned)
    for group_key, display_name, patterns in _KNOWN_GROUPS:
        if any(re.search(pattern, normalized) for pattern in patterns):
            return QuerySpec(group_key, display_name, cleaned)

    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_") or "custom"
    return QuerySpec(f"custom_{slug}", f"Custom: {cleaned or 'query'}", cleaned)


def query_specs_for_terms(query_terms: list[str]) -> list[QuerySpec]:
    """Return one stable query specification per non-empty configured term."""
    return [query_spec_for_term(term) for term in query_terms if str(term).strip()]
