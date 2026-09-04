"""Database-facing preview and application workflow for relevance rules."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
from uuid import uuid4

from ..config.settings import DEFAULT_UNWANTED_KEYWORDS
from .database import JobDatabase
from .relevance import RULESET_VERSION, classify_relevance, decision_for_result
from .time_utils import utc_now_iso


def _default_unwanted_keywords() -> list[str]:
    return [item.strip() for item in DEFAULT_UNWANTED_KEYWORDS.split(",") if item.strip()]


def build_relevance_preview(
    db: JobDatabase,
    *,
    include_unmatched: bool = False,
    active_only: bool = True,
    reconcile_archive: bool = False,
    unwanted_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """Classify stored jobs without writing to SQLite."""
    where_clause = "WHERE LOWER(source) = 'linkedin'"
    if active_only:
        where_clause += " AND archived_at IS NULL"
    with db._get_connection() as conn:  # noqa: SLF001
        rows = conn.execute(
            f"""
            SELECT job_id, title, company, archived_at, archived_reason
            FROM jobs {where_clause}
            ORDER BY datetime(COALESCE(last_seen_at, scraped_at, created_at)) DESC
            """
        ).fetchall()

    job_ids = [str(row[0]) for row in rows]
    manual_overrides = db.get_manual_overrides(job_ids)
    evaluations: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    unrelated_family_counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, str]]] = defaultdict(list)
    examples_by_rule: dict[str, list[dict[str, str]]] = defaultdict(list)
    conflicts: list[dict[str, Any]] = []
    manual_overrides_summary: list[dict[str, str]] = []
    evaluated_at = utc_now_iso()
    batch_id = f"relevance_{uuid4().hex[:12]}"
    archived_state = {str(row[0]): row[3] not in (None, "") for row in rows}

    for row in rows:
        job_id, title, company = str(row[0]), str(row[1]), str(row[2])
        archived_reason = str(row[4] or "")
        result = classify_relevance(
            title,
            company=company,
            unwanted_keywords=unwanted_keywords or _default_unwanted_keywords(),
            manual_action=manual_overrides.get(job_id),
        )
        evaluation = {
            "job_id": job_id,
            **result.to_dict(),
            "evaluated_at": evaluated_at,
        }
        evaluations.append(evaluation)
        counts[result.outcome] += 1
        if result.role_family:
            family_counts[result.role_family] += 1
        if result.outcome == "unrelated" and result.matched_value:
            unrelated_family_counts[result.matched_value] += 1
        if len(examples[result.outcome]) < 8:
            examples[result.outcome].append({"title": title, "company": company})
        for rule_name in result.matches:
            if len(examples_by_rule[rule_name]) < 5:
                examples_by_rule[rule_name].append({"title": title, "company": company})
        if result.positive_matches and result.negative_matches:
            conflicts.append(
                {
                    "job_id": job_id,
                    "title": title,
                    "company": company,
                    "positive_rules": list(result.positive_matches),
                    "negative_rules": list(result.negative_matches),
                    "resolved_as": result.outcome,
                }
            )
        if result.outcome in {"manual_keep", "manual_archive"}:
            manual_overrides_summary.append(
                {
                    "job_id": job_id,
                    "title": title,
                    "company": company,
                    "action": result.outcome.removeprefix("manual_"),
                }
            )

        details = {"batch_id": batch_id, "ruleset_version": RULESET_VERSION}
        if result.outcome == "excluded":
            decisions.append(
                {
                    "job_id": job_id,
                    "decision_source": "rule",
                    "decision_action": "archive",
                    "filter_name": "scope_exclusion",
                    "matched_value": result.matched_value,
                    "reason": result.reason,
                    "details": details,
                }
            )
        elif result.outcome == "manual_archive":
            decisions.append(
                {
                    "job_id": job_id,
                    "decision_source": "manual",
                    "decision_action": "archive",
                    "filter_name": "manual_archive_enforced",
                    "matched_value": "manual",
                    "reason": result.reason,
                    "details": details,
                }
            )
        elif reconcile_archive and archived_state[job_id]:
            if result.outcome == "manual_keep":
                decisions.append(
                    {
                        "job_id": job_id,
                        "decision_source": "manual",
                        "decision_action": "restore",
                        "filter_name": "manual_keep_enforced",
                        "matched_value": "manual_keep",
                        "reason": f"Restored by full relevance reconciliation: {result.reason}",
                        "details": details,
                    }
                )
            elif archived_reason.lower().startswith("duplicate"):
                decisions.append(
                    {
                        "job_id": job_id,
                        "decision_source": "system",
                        "decision_action": "shadow",
                        "filter_name": "system_archive_preserved",
                        "matched_value": "duplicate",
                        "reason": "Preserved an existing duplicate-record archive",
                        "details": details,
                    }
                )
            elif result.outcome in {"target", "unmatched"}:
                decisions.append(
                    {
                        "job_id": job_id,
                        "decision_source": "rule",
                        "decision_action": "restore",
                        "filter_name": "relevance_reconcile",
                        "matched_value": result.role_family or result.outcome,
                        "reason": f"Restored by full relevance reconciliation: {result.reason}",
                        "details": details,
                    }
                )
            else:
                decision = decision_for_result(
                    job_id,
                    result,
                    mode="enforce",
                    include_unmatched=include_unmatched,
                    details=details,
                )
                if decision is not None:
                    decisions.append(decision)
        else:
            decision = decision_for_result(
                job_id,
                result,
                mode="enforce",
                include_unmatched=include_unmatched,
                details=details,
            )
            if decision is not None:
                decisions.append(decision)

    archive_ids = {
        str(decision["job_id"])
        for decision in decisions
        if decision.get("decision_action") == "archive"
    }
    restore_ids = {
        str(decision["job_id"])
        for decision in decisions
        if decision.get("decision_action") == "restore"
    }
    would_archive_ids = {job_id for job_id in archive_ids if not archived_state.get(job_id, False)}
    would_restore_ids = {job_id for job_id in restore_ids if archived_state.get(job_id, False)}
    current_active = sum(1 for is_archived in archived_state.values() if not is_archived)
    return {
        "ruleset_version": RULESET_VERSION,
        "batch_id": batch_id,
        "active_only": active_only,
        "reconcile_archive": reconcile_archive,
        "include_unmatched": include_unmatched,
        "total": len(rows),
        "counts": dict(counts),
        "role_families": dict(family_counts),
        "unrelated_families": dict(unrelated_family_counts),
        "currently_active": current_active,
        "currently_archived": len(rows) - current_active,
        "would_archive": len(would_archive_ids),
        "would_restore": len(would_restore_ids),
        "final_active": current_active - len(would_archive_ids) + len(would_restore_ids),
        "final_archived": len(rows) - current_active + len(would_archive_ids) - len(would_restore_ids),
        "examples": dict(examples),
        "examples_by_rule": dict(examples_by_rule),
        "conflicts": conflicts,
        "manual_overrides": manual_overrides_summary,
        "evaluations": evaluations,
        "decisions": decisions,
    }


def apply_relevance_preview(db: JobDatabase, preview: dict[str, Any]) -> dict[str, int]:
    """Apply a previously built preview as one transaction."""
    return db.apply_relevance_evaluations(
        list(preview.get("evaluations") or []),
        list(preview.get("decisions") or []),
    )
