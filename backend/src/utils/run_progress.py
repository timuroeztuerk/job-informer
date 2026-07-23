"""Small, best-effort progress writer for API-launched backend runs.

The command-line workflow remains usable without the API.  When the API starts
one of those commands it adds ``JOB_INFORMER_API_RUN_ID`` to the environment;
this module then records concise, structured progress in the API run store.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Mapping

from .sqlite_connection import open_sqlite
from .time_utils import utc_now_iso


MAX_RECENT_EVENTS = 8


def _run_store_path() -> Path:
    configured = os.getenv("RUN_STORE_DB_PATH") or os.getenv("JOBS_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "jobs.db"


def _safe_metrics(metrics: Mapping[str, int] | None) -> dict[str, int] | None:
    if not metrics:
        return None
    result: dict[str, int] = {}
    for key, value in metrics.items():
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return result or None


def update_api_run_progress(
    *,
    stage: str,
    label: str,
    current_source: str | None = None,
    completed_sources: int | None = None,
    total_sources: int | None = None,
    metrics: Mapping[str, int] | None = None,
    event: str | None = None,
    event_level: str = "info",
) -> bool:
    """Persist a small progress snapshot for the current API-owned run.

    Progress is deliberately best-effort: a normal CLI invocation has no run
    identifier, and a temporary SQLite issue must never stop collection.
    """
    run_id = os.getenv("JOB_INFORMER_API_RUN_ID", "").strip()
    if not run_id:
        return False

    try:
        with open_sqlite(_run_store_path()) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(api_runs)")}
            required_columns = {"progress_json", "progress_updated_at"}
            if not required_columns.issubset(columns):
                return False

            row = conn.execute(
                "SELECT progress_json FROM api_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return False

            try:
                progress = json.loads(row[0] or "{}")
            except (TypeError, json.JSONDecodeError):
                progress = {}
            if not isinstance(progress, dict):
                progress = {}

            now = utc_now_iso()
            previous_label = str(progress.get("label") or "")
            progress["stage"] = stage
            progress["label"] = label
            progress["updated_at"] = now
            if current_source is not None:
                progress["current_source"] = current_source
            if completed_sources is not None:
                progress["completed_sources"] = max(0, int(completed_sources))
            if total_sources is not None:
                progress["total_sources"] = max(0, int(total_sources))
            safe_metrics = _safe_metrics(metrics)
            if safe_metrics is not None:
                progress["metrics"] = safe_metrics

            events = progress.get("events")
            if not isinstance(events, list):
                events = []
            message = event or (label if label != previous_label else None)
            if message:
                next_event = {"at": now, "level": event_level, "message": message}
                if not events or events[-1].get("message") != message:
                    events.append(next_event)
            progress["events"] = events[-MAX_RECENT_EVENTS:]

            conn.execute(
                """
                UPDATE api_runs
                SET progress_json = ?, progress_updated_at = ?
                WHERE run_id = ?
                """,
                (json.dumps(progress), now, run_id),
            )
        return True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return False
