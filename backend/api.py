#!/usr/bin/env python3
"""Local API for collection, job review, descriptions, and Intelligence."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
import re
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Iterator, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from loguru import logger
from starlette.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Ensure local src imports work when running `uvicorn api:app`
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.utils.database import JobDatabase  # noqa: E402
from src.descriptions.service import DescriptionService  # noqa: E402
from src.descriptions.linkedin import DescriptionError  # noqa: E402
from src.ai.service import AIService  # noqa: E402
from src.utils.job_consolidation import canonical_job_id, member_job_ids  # noqa: E402
from src.utils.collection_scope import build_collection_scope_comparison  # noqa: E402
from src.utils.db_summary import build_db_summary, compute_collection_freshness  # noqa: E402
from src.utils.locations import primary_location  # noqa: E402
from src.utils.relevance_service import AUTO_ARCHIVED_SQL  # noqa: E402
from src.utils.sqlite_connection import open_sqlite  # noqa: E402
from src.utils.time_utils import as_utc_datetime, normalize_utc_iso, utc_now, utc_now_iso  # noqa: E402


DEFAULT_DB = ROOT / "data" / "jobs.db"
ANSI_ESCAPE_RE = re.compile(r"(?:\x1B[@-Z\\-_]|\x1B\[[0-?]*[ -/]*[@-~])")


def _as_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _resolve_db_path(env_name: str, fallback: Path) -> str:
    env_path = os.getenv(env_name)
    candidate = Path(env_path) if env_path else fallback
    try:
        resolved = candidate.expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return str(resolved)
    except OSError as exc:
        source = "configured" if env_path else "default"
        raise RuntimeError(
            f"Unable to initialize {source} database path for {env_name}: {candidate}: {exc}"
        ) from exc


DB_PATH = _resolve_db_path("JOBS_DB_PATH", DEFAULT_DB)
RUN_STORE_DB_PATH = _resolve_db_path("RUN_STORE_DB_PATH", Path(DB_PATH))
API_TOKEN = os.getenv("API_TOKEN", "")
MAX_LOG_BYTES = _as_int_env("MAX_LOG_BYTES", 30000)  # Avoid sending huge logs to the frontend
APP_INSTANCE_NAME = os.getenv("APP_INSTANCE_NAME", "job-informer-local").strip() or "job-informer-local"

FRONTEND_DIST_DIR = ROOT / "frontend" / "dist"
REQUIRED_DB_TABLES = frozenset(
    {
        "collection_queries",
        "filter_decisions",
        "job_observations",
        "job_query_matches",
        "jobs",
        "query_groups",
        "query_terms",
        "relevance_validation_labels",
        "scrape_runs",
        "description_sources", "description_extractions", "description_fetches", "description_queue_state",
        "ai_work", "ai_attempts", "ai_queue_state",
    }
)


@dataclass(frozen=True)
class AppSettings:
    db_path: str
    run_store_db_path: str
    api_token: str
    max_log_bytes: int
    frontend_dist_dir: Path
    instance_name: str = "job-informer-local"


APP_SETTINGS = AppSettings(
    db_path=DB_PATH,
    run_store_db_path=RUN_STORE_DB_PATH,
    api_token=API_TOKEN,
    max_log_bytes=MAX_LOG_BYTES,
    frontend_dist_dir=FRONTEND_DIST_DIR,
    instance_name=APP_INSTANCE_NAME,
)


@contextmanager
def open_jobs_db() -> Iterator[sqlite3.Connection]:
    with open_sqlite(APP_SETTINGS.db_path, row_factory=sqlite3.Row) as conn:
        yield conn


def database_readiness(
    db_path: str | Path | None = None,
    *,
    instance_name: str | None = None,
) -> dict[str, Any]:
    """Return database identity and schema diagnostics without mutating schema."""
    resolved_path = Path(db_path or APP_SETTINGS.db_path).expanduser().resolve()
    result: dict[str, Any] = {
        "status": "not_ready",
        "instance_name": instance_name or APP_SETTINGS.instance_name,
        "db_path": str(resolved_path),
        "schema_ok": False,
        "missing_tables": sorted(REQUIRED_DB_TABLES),
        "total_jobs": 0,
        "active_jobs": 0,
        "db_size_bytes": resolved_path.stat().st_size if resolved_path.is_file() else 0,
        "db_modified_at": (
            datetime.fromtimestamp(resolved_path.stat().st_mtime, tz=UTC).isoformat()
            if resolved_path.is_file()
            else None
        ),
    }
    if not resolved_path.is_file():
        result["error"] = "Database file does not exist."
        return result
    try:
        with open_sqlite(resolved_path, row_factory=sqlite3.Row) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            missing_tables = sorted(REQUIRED_DB_TABLES - tables)
            result["missing_tables"] = missing_tables
            result["schema_ok"] = not missing_tables
            if "jobs" in tables:
                counts = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total_jobs,
                        SUM(CASE WHEN archived_at IS NULL THEN 1 ELSE 0 END) AS active_jobs
                    FROM jobs
                    """
                ).fetchone()
                result["total_jobs"] = int(counts["total_jobs"] or 0)
                result["active_jobs"] = int(counts["active_jobs"] or 0)
            if result["schema_ok"]:
                result["status"] = "ready"
    except (OSError, sqlite3.Error) as exc:
        result["error"] = str(exc)
    return result


@asynccontextmanager
async def app_lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    global job_db, run_store, description_service, ai_service
    job_db = JobDatabase(APP_SETTINGS.db_path)
    description_service = DescriptionService(job_db.db_path)
    description_service.store.recover()
    ai_service = AIService(job_db.db_path)
    run_store = RunStore(APP_SETTINGS.run_store_db_path)
    readiness = database_readiness()
    logger.info(
        "Database {} at {}: {} total jobs, {} active jobs",
        readiness["status"],
        readiness["db_path"],
        readiness["total_jobs"],
        readiness["active_jobs"],
    )
    run_store.interrupt_unfinished()
    try:
        yield
    finally:
        await ai_service.close()
        description_service.close()
        for record in list(RUNS.values()):
            if record.process.poll() is None:
                record.process.terminate()
                try:
                    record.process.wait(5)
                except subprocess.TimeoutExpired:
                    record.process.kill()
                run_store.update_status(record.run_id, "interrupted", None, utc_now())
        RUNS.clear()
        job_db = None
        run_store = None
        description_service = None
        ai_service = None


app = FastAPI(title="Job Informer API", version="0.1.0", lifespan=app_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check() -> dict:
    return {
        "status": "ok",
        "instance_name": APP_SETTINGS.instance_name,
    }


@app.get("/ready")
def readiness_check(response: Response) -> dict:
    readiness = database_readiness()
    if readiness["status"] != "ready":
        response.status_code = 503
    return readiness

job_db: Optional[JobDatabase] = None
run_store: Optional["RunStore"] = None
description_service: Optional[DescriptionService] = None
ai_service: Optional[AIService] = None


def _ai() -> AIService:
    if ai_service is None:
        raise HTTPException(status_code=503, detail="AI extraction is not initialized yet.")
    return ai_service


def _descriptions() -> DescriptionService:
    if description_service is None:
        raise HTTPException(status_code=503, detail="Description retrieval is not initialized yet.")
    return description_service


@contextmanager
def _description_errors():
    try:
        yield
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, DescriptionError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def _require_job_db() -> JobDatabase:
    if job_db is None:
        raise HTTPException(status_code=503, detail="Job database is not initialized yet.")
    return job_db


def _require_run_store() -> "RunStore":
    if run_store is None:
        raise HTTPException(status_code=503, detail="Run store is not initialized yet.")
    return run_store


def require_token(x_api_token: str | None = Header(default=None)):
    """Optional bearer: if API_TOKEN is set, enforce it via header."""
    if APP_SETTINGS.api_token and x_api_token != APP_SETTINGS.api_token:
        raise HTTPException(status_code=401, detail="Invalid or missing API token.")
    return True


DEFAULT_MODE = "run-once"


class RunRequest(BaseModel):
    mode: Literal["run-once"] = DEFAULT_MODE
    keywords: Optional[str] = None
    locations: Optional[str] = None
    time_range: Optional[str] = None


class FavoriteUpdate(BaseModel):
    is_favorite: bool


class FlagUpdate(BaseModel):
    is_flagged: bool
    flag_reason: Optional[str] = Field(default=None, max_length=2000)


class RunProgressEvent(BaseModel):
    at: datetime
    level: Literal["info", "warning", "error"] = "info"
    message: str


class RunProgress(BaseModel):
    stage: str
    label: str
    current_query: Optional[str] = None
    completed_queries: Optional[int] = None
    total_queries: Optional[int] = None
    metrics: Optional[dict[str, int]] = None
    updated_at: datetime
    events: list[RunProgressEvent] = Field(default_factory=list)


class RunSummary(BaseModel):
    run_id: str
    mode: str
    status: str
    return_code: Optional[int] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    keywords: Optional[str] = None
    locations: Optional[str] = None
    time_range: Optional[str] = None
    trigger: str = "manual"
    pid: Optional[int] = None
    metrics: Optional[dict[str, int]] = None
    query_coverage: Optional[list[dict[str, Any]]] = None
    scope_comparison: Optional[dict[str, Any]] = None
    progress: Optional[RunProgress] = None


class RunStatus(RunSummary):
    log_tail: str = ""


class RunStore:
    """Lightweight persistence for run history."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_table()

    def _connect(self):
        return open_sqlite(self.db_path)

    def _ensure_table(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_runs (
                    run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL DEFAULT 'run-once',
                    status TEXT NOT NULL,
                    return_code INTEGER,
                    log_path TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    keywords TEXT,
                    locations TEXT,
                    time_range TEXT,
                    trigger TEXT NOT NULL DEFAULT 'manual',
                    pid INTEGER,
                    progress_json TEXT NOT NULL DEFAULT '{}',
                    progress_updated_at TEXT
                )
                """
            )
            # Backfill missing columns for existing installs
            columns = {row[1] for row in conn.execute("PRAGMA table_info(api_runs)")}
            if "mode" not in columns:
                conn.execute("ALTER TABLE api_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'run-once'")
            if "trigger" not in columns:
                conn.execute("ALTER TABLE api_runs ADD COLUMN trigger TEXT NOT NULL DEFAULT 'manual'")
            if "pid" not in columns:
                conn.execute("ALTER TABLE api_runs ADD COLUMN pid INTEGER")
            if "progress_json" not in columns:
                conn.execute("ALTER TABLE api_runs ADD COLUMN progress_json TEXT NOT NULL DEFAULT '{}'")
            if "progress_updated_at" not in columns:
                conn.execute("ALTER TABLE api_runs ADD COLUMN progress_updated_at TEXT")
            conn.commit()

    def claim_start(
        self,
        run_id: str,
        log_path: Path,
        mode: str,
        keywords: Optional[str],
        locations: Optional[str],
        time_range: Optional[str],
        trigger: str = "manual",
        started_at: Optional[datetime] = None,
    ) -> Optional[dict]:
        start_time = normalize_utc_iso(started_at, naive_policy="utc") or utc_now_iso()
        initial_progress = {
            "stage": "starting",
            "label": "Preparing run",
            "current_query": None,
            "completed_queries": None,
            "total_queries": None,
            "metrics": None,
            "updated_at": start_time,
            "events": [
                {
                    "at": start_time,
                    "level": "info",
                    "message": "Run queued",
                }
            ],
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.row_factory = sqlite3.Row
            active = conn.execute(
                """
                SELECT * FROM api_runs
                WHERE status IN ('starting', 'running')
                ORDER BY datetime(started_at) DESC
                LIMIT 1
                """
            ).fetchone()
            if active:
                conn.rollback()
                return {key: active[key] for key in active.keys()}
            conn.execute(
                """
                INSERT OR REPLACE INTO api_runs (
                    run_id, mode, status, return_code, log_path, started_at, finished_at,
                    keywords, locations, time_range, trigger, pid, progress_json, progress_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    mode,
                    "starting",
                    None,
                    str(log_path),
                    start_time,
                    None,
                    keywords,
                    locations,
                    time_range,
                    trigger,
                    None,
                    json.dumps(initial_progress),
                    start_time,
                ),
            )
            conn.commit()
        return None

    def mark_running(self, run_id: str, pid: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE api_runs SET status = 'running', pid = ? WHERE run_id = ?",
                (pid, run_id),
            )
            conn.commit()

    def update_status(
        self,
        run_id: str,
        status: str,
        return_code: Optional[int],
        finished_at: Optional[datetime],
    ) -> None:
        with self._connect() as conn:
            if finished_at is None:
                conn.execute(
                    "UPDATE api_runs SET status = ?, return_code = ? WHERE run_id = ?",
                    (status, return_code, run_id),
                )
            else:
                finished_at_iso = normalize_utc_iso(finished_at, naive_policy="utc")
                terminal_progress = self._terminal_progress_json(
                    conn,
                    run_id,
                    status,
                    finished_at_iso or utc_now_iso(),
                )
                conn.execute(
                    """
                    UPDATE api_runs
                    SET status = ?, return_code = ?, finished_at = ?, pid = NULL,
                        progress_json = ?, progress_updated_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        status,
                        return_code,
                        finished_at_iso,
                        terminal_progress,
                        finished_at_iso,
                        run_id,
                    ),
                )
            conn.commit()

    @staticmethod
    def _terminal_progress_json(
        conn: sqlite3.Connection,
        run_id: str,
        status: str,
        timestamp: str,
    ) -> str:
        row = conn.execute(
            "SELECT progress_json FROM api_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        try:
            progress = json.loads(row[0] or "{}") if row else {}
        except (TypeError, json.JSONDecodeError):
            progress = {}
        if not isinstance(progress, dict):
            progress = {}

        existing_events = progress.get("events")
        events = existing_events if isinstance(existing_events, list) else []
        terminal_stage = "completed" if status == "succeeded" else status
        fallback_label = "Run completed" if status == "succeeded" else f"Run {status}"
        is_new_terminal_state = progress.get("stage") not in {terminal_stage, status}
        label = fallback_label if is_new_terminal_state else str(progress.get("label") or fallback_label)
        if is_new_terminal_state:
            events.append(
                {
                    "at": timestamp,
                    "level": "info" if status == "succeeded" else "error",
                    "message": fallback_label,
                }
            )
        progress.update(
            {
                "stage": terminal_stage,
                "label": label,
                "updated_at": timestamp,
                "events": events[-8:],
            }
        )
        return json.dumps(progress)

    def get(self, run_id: str) -> Optional[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM api_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        return {k: row[k] for k in row.keys()}

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM api_runs ORDER BY datetime(started_at) DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{k: row[k] for k in row.keys()} for row in rows]

    def interrupt_unfinished(self) -> int:
        """Mark runs from a previous API process as interrupted on startup."""
        now = utc_now_iso()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id FROM api_runs WHERE status IN ('starting', 'running')"
            ).fetchall()
            for row in rows:
                run_id = str(row[0])
                conn.execute(
                    """
                    UPDATE api_runs
                    SET status = 'interrupted', finished_at = ?, pid = NULL,
                        progress_json = ?, progress_updated_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        now,
                        self._terminal_progress_json(conn, run_id, "interrupted", now),
                        now,
                        run_id,
                    ),
                )
            conn.commit()
            return len(rows)

def _metrics_for_api_run(run_id: str) -> Optional[dict[str, int]]:
    if not run_id:
        return None
    try:
        with open_jobs_db() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(scrape_runs)")}
            if "api_run_id" not in columns:
                return None
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(observed_jobs_count), 0),
                    COALESCE(SUM(new_jobs_count), 0),
                    COALESCE(SUM(archived_jobs_count), 0),
                    COUNT(*)
                FROM scrape_runs
                WHERE api_run_id = ?
                """,
                (run_id,),
            ).fetchone()
            coverage_rows = (
                conn.execute(
                    "SELECT coverage_json FROM scrape_runs WHERE api_run_id = ?",
                    (run_id,),
                ).fetchall()
                if "coverage_json" in columns
                else []
            )
            relevance_rows = (
                conn.execute(
                    "SELECT relevance_json FROM scrape_runs WHERE api_run_id = ?",
                    (run_id,),
                ).fetchall()
                if "relevance_json" in columns
                else []
            )
        if not row or int(row[3] or 0) == 0:
            return None
        metrics = {
            "observed": int(row[0] or 0),
            "new": int(row[1] or 0),
            "archived": int(row[2] or 0),
        }
        reports: list[dict[str, Any]] = []
        for coverage_row in coverage_rows:
            try:
                payload = json.loads(coverage_row[0] or "[]")
            except (TypeError, json.JSONDecodeError):
                payload = []
            if isinstance(payload, list):
                reports.extend(report for report in payload if isinstance(report, dict))
        if reports:
            metrics.update(
                {
                    "queries": len(reports),
                    "pages_attempted": sum(int(report.get("pages_attempted", 0) or 0) for report in reports),
                    "pages_completed": sum(int(report.get("pages_completed", 0) or 0) for report in reports),
                    "request_failures": sum(int(report.get("request_failures", 0) or 0) for report in reports),
                    "rate_limit_responses": sum(int(report.get("rate_limit_responses", 0) or 0) for report in reports),
                }
            )
        for relevance_row in relevance_rows:
            try:
                relevance_metrics = json.loads(relevance_row[0] or "{}")
            except (TypeError, json.JSONDecodeError):
                relevance_metrics = {}
            if isinstance(relevance_metrics, dict):
                for key, value in relevance_metrics.items():
                    try:
                        metrics[str(key)] = metrics.get(str(key), 0) + int(value)
                    except (TypeError, ValueError):
                        continue
        return metrics
    except sqlite3.Error:
        return None


def _query_coverage_for_api_run(run_id: str) -> list[dict[str, Any]]:
    """Return exact normalized query diagnostics for one API-launched run."""
    if not run_id:
        return []
    try:
        with open_jobs_db() as conn:
            if not {
                "collection_queries",
                "scrape_runs",
            }.issubset(
                {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
            ):
                return []
            rows = conn.execute(
                """
                SELECT cq.query_group_key, qg.display_name, cq.query_text, cq.location,
                       cq.page_offsets_json, cq.pages_attempted, cq.pages_completed,
                       cq.raw_cards, cq.valid_jobs, cq.duplicate_cards,
                       cq.request_failures, cq.rate_limit_responses,
                       cq.stop_reason, cq.last_status, cq.last_error
                FROM collection_queries cq
                INNER JOIN scrape_runs sr ON sr.scrape_run_id = cq.scrape_run_id
                LEFT JOIN query_groups qg ON qg.query_group_key = cq.query_group_key
                WHERE sr.api_run_id = ?
                ORDER BY cq.collection_query_id
                """,
                (run_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = _row_to_dict(row)
            try:
                item["page_offsets"] = json.loads(item.pop("page_offsets_json") or "[]")
            except (TypeError, json.JSONDecodeError):
                item["page_offsets"] = []
            result.append(item)
        return result
    except sqlite3.Error:
        return []


def _scope_comparison_for_api_run(run_id: str) -> Optional[dict[str, Any]]:
    """Compare distinct country-wide and retained-city results for one run."""
    if not run_id:
        return None
    try:
        with open_jobs_db() as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if not {
                "collection_queries",
                "job_query_matches",
                "scrape_runs",
            }.issubset(tables):
                return None
            query_rows = [
                _row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT cq.location, cq.pages_attempted, cq.pages_completed,
                           cq.request_failures
                    FROM collection_queries cq
                    INNER JOIN scrape_runs sr ON sr.scrape_run_id = cq.scrape_run_id
                    WHERE sr.api_run_id = ?
                    """,
                    (run_id,),
                ).fetchall()
            ]
            match_rows = [
                _row_to_dict(row)
                for row in conn.execute(
                    """
                    SELECT jqm.job_id, cq.location
                    FROM job_query_matches jqm
                    INNER JOIN collection_queries cq
                        ON cq.collection_query_id = jqm.collection_query_id
                    INNER JOIN scrape_runs sr ON sr.scrape_run_id = cq.scrape_run_id
                    WHERE sr.api_run_id = ?
                    """,
                    (run_id,),
                ).fetchall()
            ]
        return build_collection_scope_comparison(query_rows, match_rows)
    except sqlite3.Error:
        return None


def _progress_for_run(record: dict[str, Any]) -> Optional[RunProgress]:
    raw_progress = record.get("progress_json")
    if not raw_progress:
        return None
    try:
        payload = json.loads(raw_progress)
        return RunProgress.model_validate(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None



class RunRecord:
    """Holds lightweight state for a background run."""

    def __init__(
        self,
        run_id: str,
        process: subprocess.Popen,
        log_path: Path,
        mode: str,
        keywords: Optional[str],
        locations: Optional[str],
        time_range: Optional[str],
        trigger: str = "manual",
        started_at: Optional[datetime] = None,
    ):
        self.run_id = run_id
        self.process = process
        self.log_path = log_path
        self.mode = mode
        self.started_at = as_utc_datetime(started_at, naive_policy="utc") or utc_now()
        self.finished_at: Optional[datetime] = None
        self.keywords = keywords
        self.locations = locations
        self.time_range = time_range
        self.trigger = trigger
        self._run_store = _require_run_store()

    def status(self) -> RunStatus:
        code = self.process.poll()
        status = "running"
        finished = None
        if code is not None:
            finished = self.finished_at or utc_now()
            self.finished_at = finished
            status = "succeeded" if code == 0 else "failed"
            self._run_store.update_status(self.run_id, status, code, finished)
        else:
            self._run_store.update_status(self.run_id, status, None, None)
        return RunStatus(
            run_id=self.run_id,
            mode=self.mode,
            status=status,
            return_code=code,
            log_tail=_read_log_tail(self.log_path),
            started_at=self.started_at,
            finished_at=finished,
            keywords=self.keywords,
            locations=self.locations,
            time_range=self.time_range,
            trigger=self.trigger,
            pid=self.process.pid if code is None else None,
            metrics=_metrics_for_api_run(self.run_id),
            query_coverage=_query_coverage_for_api_run(self.run_id),
            scope_comparison=_scope_comparison_for_api_run(self.run_id),
            progress=_progress_for_run(self._run_store.get(self.run_id) or {}),
        )


# In-memory run registry; cleared when the server restarts.
RUNS: dict[str, RunRecord] = {}


@app.post("/runs", response_model=RunStatus)
def start_run(payload: RunRequest, _: bool = Depends(require_token)) -> RunStatus:
    """
    Start one manual collection in the background.
    Optional overrides: keywords, locations, time_range.
    """
    return _launch_run(payload, trigger="manual")


def _launch_run(payload: RunRequest, *, trigger: str) -> RunStatus:
    """Atomically claim and launch one backend command."""
    for record in list(RUNS.values()):
        record.status()

    run_id = uuid.uuid4().hex
    log_path = ROOT / "logs" / f"api_run_{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    mode = DEFAULT_MODE
    started_at = utc_now()
    active = _require_run_store().claim_start(
        run_id,
        log_path,
        mode,
        payload.keywords,
        payload.locations,
        payload.time_range,
        trigger,
        started_at,
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"Run {active['run_id']} is already {active['status']}; wait for it to finish.",
        )

    cmd = [sys.executable, "-u", str(ROOT / "main.py")]
    if payload.keywords:
        cmd += ["--keywords", payload.keywords]
    if payload.locations:
        cmd += ["--locations", payload.locations]
    if payload.time_range:
        cmd += ["--time", payload.time_range]

    run_env = os.environ.copy()
    run_env["PYTHONUNBUFFERED"] = "1"
    run_env["JOB_INFORMER_API_RUN_ID"] = run_id
    run_env["RUN_STORE_DB_PATH"] = _require_run_store().db_path

    try:
        with log_path.open("w") as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=run_env,
            )
    except Exception:
        _require_run_store().update_status(run_id, "failed", None, utc_now())
        raise

    _require_run_store().mark_running(run_id, process.pid)
    RUNS[run_id] = RunRecord(
        run_id,
        process,
        log_path,
        mode,
        keywords=payload.keywords,
        locations=payload.locations,
        time_range=payload.time_range,
        trigger=trigger,
        started_at=started_at,
    )
    return RUNS[run_id].status()


@app.get("/runs/{run_id}", response_model=RunStatus)
def get_run_status(run_id: str, _: bool = Depends(require_token)) -> RunStatus:
    """Return current status and recent log output for a run."""
    record = RUNS.get(run_id)
    if record:
        return record.status()

    stored = _require_run_store().get(run_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Run not found.")

    finished = as_utc_datetime(stored.get("finished_at"), naive_policy="utc")
    started = as_utc_datetime(stored.get("started_at"), naive_policy="utc") or utc_now()
    return RunStatus(
        run_id=stored["run_id"],
        mode=DEFAULT_MODE,
        status=stored["status"],
        return_code=stored["return_code"],
        log_tail=_read_log_tail(Path(stored["log_path"])),
        started_at=started,
        finished_at=finished,
        keywords=stored.get("keywords"),
        locations=stored.get("locations"),
        time_range=stored.get("time_range"),
        trigger=stored.get("trigger") or "manual",
        pid=stored.get("pid"),
        metrics=_metrics_for_api_run(run_id),
        query_coverage=_query_coverage_for_api_run(run_id),
        scope_comparison=_scope_comparison_for_api_run(run_id),
        progress=_progress_for_run(stored),
    )


@app.get("/runs", response_model=list[RunSummary])
def list_runs(limit: int = Query(20, ge=1, le=100), _: bool = Depends(require_token)) -> list[RunSummary]:
    """Return recent runs (newest first)."""
    # Refresh statuses for in-memory tracked runs
    for record in list(RUNS.values()):
        record.status()
    records = _require_run_store().list_recent(limit=limit)
    summaries: list[RunSummary] = []
    for record in records:
        summaries.append(
            RunSummary(
                run_id=record["run_id"],
                mode=DEFAULT_MODE,
                status=record["status"],
                return_code=record["return_code"],
                started_at=as_utc_datetime(record["started_at"], naive_policy="utc") or utc_now(),
                finished_at=as_utc_datetime(record.get("finished_at"), naive_policy="utc"),
                keywords=record.get("keywords"),
                locations=record.get("locations"),
                time_range=record.get("time_range"),
                trigger=record.get("trigger") or "manual",
                pid=record.get("pid"),
                metrics=_metrics_for_api_run(record["run_id"]),
                query_coverage=_query_coverage_for_api_run(record["run_id"]),
                scope_comparison=_scope_comparison_for_api_run(record["run_id"]),
                progress=_progress_for_run(record),
            )
        )
    return summaries


@app.get("/jobs")
def list_jobs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    location: Optional[str] = None,
    source: Optional[str] = None,
    company: Optional[str] = None,
    company_exact: bool = False,
    location_primary: bool = False,
    last_seen_from: Optional[str] = None,
    repeated: bool = False,
    flagged: bool = False,
    role_family: Optional[str] = None,
    query_group: Optional[str] = None,
    relevance_outcome: Optional[Literal[
        "target", "excluded", "unrelated", "unmatched", "manual_keep", "manual_archive", "auto_archived"
    ]] = None,
    favorite: bool = Query(default=False, description="Return only favorite jobs"),
    archived: Literal["exclude", "include", "only"] = Query(default="exclude"),
    date_from: Optional[str] = Query(default=None, description="ISO date/time lower bound"),
    date_to: Optional[str] = Query(default=None, description="ISO date/time upper bound"),
    sort: str = Query(
        default="scraped_at_desc",
        description="scraped_at_desc|scraped_at_asc|last_seen_desc|last_seen_asc|seen_count_desc|seen_count_asc|title_asc|title_desc",
    ),
    _: bool = Depends(require_token),
) -> dict:
    """Return a simple paginated job list with optional filters."""
    clauses = ["LOWER(j.source) = 'linkedin'"]
    params: list[str] = []

    if relevance_outcome == "auto_archived":
        clauses.append(AUTO_ARCHIVED_SQL)
    elif archived == "exclude":
        clauses.append("j.archived_at IS NULL")
    elif archived == "only":
        clauses.append("j.archived_at IS NOT NULL")

    if favorite:
        clauses.append("j.is_favorite = 1")
    if flagged:
        clauses.append("j.is_flagged = 1")

    if search:
        like = f"%{search}%"
        clauses.append("(j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ?)")
        params.extend([like, like, like])
    if location:
        match = "primary_location(p.location) = ?" if location_primary else "p.location LIKE ?"
        clauses.append(f"""EXISTS (SELECT 1 FROM job_memberships m JOIN jobs p USING(job_id)
            WHERE m.canonical_job_id = j.job_id AND {match})""")
        params.append(primary_location(location) if location_primary else f"%{location}%")
    if source:
        clauses.append("j.source = ?")
        params.append(source)
    if company:
        clauses.append("COALESCE(NULLIF(j.company, ''), 'Unknown') = ?" if company_exact else "j.company LIKE ?")
        params.append(company if company_exact else f"%{company}%")
    if role_family:
        if role_family == "unclassified":
            clauses.append("(j.role_family IS NULL OR j.role_family = '')")
        else:
            clauses.append("j.role_family = ?")
            params.append(role_family)
    if last_seen_from:
        clauses.append("datetime(COALESCE(j.last_seen_at, j.scraped_at, j.created_at)) >= datetime(?)")
        params.append(last_seen_from)
    if repeated:
        clauses.append("COALESCE(j.seen_count, 1) > 1")
    if relevance_outcome and relevance_outcome != "auto_archived":
        clauses.append("j.relevance_outcome = ?")
        params.append(relevance_outcome)
    if query_group:
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM job_query_matches jqm_filter
                INNER JOIN collection_queries cq_filter
                    ON cq_filter.collection_query_id = jqm_filter.collection_query_id
                WHERE jqm_filter.job_id IN (SELECT job_id FROM job_memberships WHERE canonical_job_id = j.job_id)
                  AND cq_filter.query_group_key = ?
            )
            """
        )
        params.append(query_group)
    if date_from:
        clauses.append("datetime(COALESCE(j.scraped_at, j.created_at)) >= datetime(?)")
        params.append(date_from)
    if date_to:
        clauses.append("datetime(COALESCE(j.scraped_at, j.created_at)) <= datetime(?)")
        params.append(date_to)

    sort_map = {
        "scraped_at_desc": "datetime(COALESCE(j.scraped_at, j.created_at)) DESC",
        "scraped_at_asc": "datetime(COALESCE(j.scraped_at, j.created_at)) ASC",
        "last_seen_desc": "datetime(COALESCE(j.last_seen_at, j.scraped_at, j.created_at)) DESC",
        "last_seen_asc": "datetime(COALESCE(j.last_seen_at, j.scraped_at, j.created_at)) ASC",
        "seen_count_desc": "COALESCE(j.seen_count, 1) DESC, datetime(COALESCE(j.last_seen_at, j.scraped_at, j.created_at)) DESC",
        "seen_count_asc": "COALESCE(j.seen_count, 1) ASC, datetime(COALESCE(j.last_seen_at, j.scraped_at, j.created_at)) ASC",
        "title_asc": "j.title COLLATE NOCASE ASC",
        "title_desc": "j.title COLLATE NOCASE DESC",
    }
    order_by = sort_map.get(sort, sort_map["scraped_at_desc"])

    where_clause = " AND ".join(clauses)
    try:
        with open_jobs_db() as conn:
            conn.create_function("primary_location", 1, primary_location, deterministic=True)
            query = f"""
                SELECT
                    j.job_id,
                    j.title,
                    j.company,
                    j.location,
                    j.source,
                    j.url,
                    j.salary,
                    j.scraped_at,
                    j.archived_at,
                    j.archived_reason,
                    j.is_favorite,
                    j.is_flagged,
                    j.flag_reason,
                    j.flagged_at,
                    j.first_seen_at,
                    j.last_seen_at,
                    j.seen_count,
                    j.relevance_outcome,
                    j.role_family,
                    j.relevance_reason,
                    j.relevance_ruleset_version,
                    j.relevance_evaluated_at,
                    j.posting_count,
                    j.postings_json,
                    (
                        SELECT GROUP_CONCAT(DISTINCT cq.query_group_key)
                        FROM job_query_matches jqm
                        INNER JOIN collection_queries cq
                            ON cq.collection_query_id = jqm.collection_query_id
                        WHERE jqm.job_id IN (SELECT job_id FROM job_memberships WHERE canonical_job_id = j.job_id)
                    ) AS query_groups_csv
                FROM review_jobs j
                WHERE {where_clause}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
            """
            rows = conn.execute(query, [*params, limit, offset]).fetchall()

            count_query = f"""
                SELECT COUNT(*)
                FROM review_jobs j
                WHERE {where_clause}
            """
            total = conn.execute(count_query, params).fetchone()[0]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    return {
        "total": total,
        "count": len(rows),
        "items": [_job_row_to_dict(row) for row in rows],
    }


class DescriptionRequest(BaseModel):
    refresh: bool = False


class AIRequest(BaseModel):
    retry: bool = False


@app.get("/ai/queue")
async def ai_queue(_: bool = Depends(require_token)) -> dict:
    return await _ai().status()


@app.post("/ai/queue")
async def queue_ai(request: AIRequest, _: bool = Depends(require_token)) -> dict:
    with _description_errors():
        return await _ai().queue(request.retry)


@app.post("/ai/queue/{action}")
async def control_ai(action: Literal["pause", "resume"], _: bool = Depends(require_token)) -> dict:
    with _description_errors():
        return await _ai().control(action)


@app.get("/jobs/{job_id:path}/extraction")
async def get_extraction(job_id: str, _: bool = Depends(require_token)) -> dict:
    with _description_errors():
        return await _ai().db(_ai().store.job_result, job_id)


@app.post("/jobs/{job_id:path}/extraction")
async def request_extraction(job_id: str, _: bool = Depends(require_token)) -> dict:
    with _description_errors():
        return await _ai().queue_job(job_id)


class DescriptionBatchRequest(BaseModel):
    selection: Literal["all", "retry"] = "all"
    limit: int = Field(default=10, ge=1, le=20)


@app.get("/descriptions/queue")
def description_queue(_: bool = Depends(require_token)) -> dict:
    return _descriptions().store.queue_status()


@app.post("/descriptions/queue")
def queue_descriptions(request: DescriptionBatchRequest, _: bool = Depends(require_token)) -> dict:
    service = _descriptions()
    with _description_errors():
        jobs = service.store.unfetched_candidates() if request.selection == "all" else service.store.failed_candidates(request.limit)
        added = service.enqueue(jobs, refresh=request.selection == "retry")
    return {**service.store.queue_status(), "added": added}


@app.post("/descriptions/queue/{action}")
def control_description_queue(action: Literal["pause", "resume"], _: bool = Depends(require_token)) -> dict:
    service = _descriptions()
    with _description_errors():
        if action == "pause":
            service.store.pause()
        else:
            service.resume()
    return service.store.queue_status()


@app.get("/jobs/{job_id:path}/description")
def get_description(job_id: str, _: bool = Depends(require_token)) -> dict:
    with _description_errors():
        return {**_descriptions().store.job_description(job_id), "queue": _descriptions().store.queue_status()}


@app.post("/jobs/{job_id:path}/description")
def request_description(job_id: str, request: DescriptionRequest, _: bool = Depends(require_token)) -> dict:
    with _description_errors():
        _descriptions().store.require_job(job_id, active_only=True)
        _descriptions().enqueue([job_id], refresh=request.refresh)
        return get_description(job_id, _=True)


@app.post("/jobs/{job_id:path}/description/reparse")
def reparse_description(job_id: str, _: bool = Depends(require_token)) -> dict:
    with _description_errors():
        return {**_descriptions().reparse(job_id), "queue": _descriptions().store.queue_status()}


@app.get("/jobs/{job_id:path}")
def get_job(
    job_id: str,
    _: bool = Depends(require_token),
) -> dict:
    """Return a single job by id."""
    try:
        with open_jobs_db() as conn:
            job_id = canonical_job_id(conn, job_id)
            row = conn.execute(
                "SELECT * FROM review_jobs WHERE job_id = ? AND LOWER(source) = 'linkedin'",
                (job_id,),
            ).fetchone()
            query_matches = conn.execute(
                """
                SELECT
                    cq.query_group_key,
                    COALESCE(qg.display_name, cq.query_group_key) AS query_group_name,
                    cq.query_text,
                    cq.location,
                    MIN(jqm.first_page_offset) AS first_page_offset,
                    MAX(sr.observed_at) AS last_matched_at,
                    COUNT(DISTINCT cq.scrape_run_id) AS match_runs
                FROM job_query_matches jqm
                INNER JOIN collection_queries cq
                    ON cq.collection_query_id = jqm.collection_query_id
                INNER JOIN scrape_runs sr ON sr.scrape_run_id = cq.scrape_run_id
                LEFT JOIN query_groups qg ON qg.query_group_key = cq.query_group_key
                WHERE jqm.job_id IN (SELECT job_id FROM job_memberships WHERE canonical_job_id = ?)
                GROUP BY cq.query_group_key, qg.display_name, cq.query_text, cq.location
                ORDER BY datetime(MAX(sr.observed_at)) DESC, cq.query_group_key, cq.query_text
                LIMIT 50
                """,
                (job_id,),
            ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _row_to_dict(row)
    job["observation"] = _observation_from_row(row)
    job["query_matches"] = [_row_to_dict(match) for match in query_matches]
    return job


@app.delete("/jobs/{job_id:path}", status_code=204)
def delete_job(job_id: str, _: bool = Depends(require_token)) -> Response:
    """Archive a job instead of hard-deleting it."""
    try:
        db = _require_job_db()
        with open_jobs_db() as conn:
            existing = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            members = member_job_ids(conn, job_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Job not found")

        archive_summary = db.archive_jobs_with_filter_decisions(
            [
                {
                    "job_id": member,
                    "decision_source": "manual",
                    "decision_action": "archive",
                    "filter_name": "manual_archive",
                    "reason": "Archived manually via API",
                }
                for member in members
            ],
            default_reason="Archived manually via API",
        )
        if int(archive_summary.get("archived", 0) or 0) == 0:
            raise HTTPException(status_code=409, detail="Job is already archived")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to archive job: {exc}") from exc
    return Response(status_code=204)


@app.post("/jobs/{job_id:path}/restore", status_code=204)
def restore_job(job_id: str, _: bool = Depends(require_token)) -> Response:
    """Restore a previously archived job to the active set."""
    try:
        with open_jobs_db() as conn:
            row = conn.execute("SELECT archived_at FROM review_jobs WHERE job_id = ?", (canonical_job_id(conn, job_id),)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if row["archived_at"] in (None, ""):
            raise HTTPException(status_code=409, detail="Job is not archived")

        restored = _require_job_db().restore_job(job_id)
        if not restored:
            raise HTTPException(status_code=500, detail="Failed to restore job")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to restore job: {exc}") from exc
    return Response(status_code=204)


@app.put("/jobs/{job_id:path}/flag")
def update_job_flag(
    job_id: str,
    update: FlagUpdate,
    _: bool = Depends(require_token),
) -> dict[str, object]:
    """Flag a mismatch example for later filter review, without archiving it."""
    try:
        result = _require_job_db().set_job_flag(job_id, update.is_flagged, update.flag_reason)
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update flag: {exc}") from exc


@app.put("/jobs/{job_id:path}/favorite")
def update_job_favorite(
    job_id: str,
    update: FavoriteUpdate,
    _: bool = Depends(require_token),
) -> dict[str, object]:
    """Set a job's favorite flag without changing its archive state."""
    try:
        updated = _require_job_db().set_job_favorite(job_id, update.is_favorite)
        if not updated:
            raise HTTPException(status_code=404, detail="Job not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update favorite: {exc}") from exc
    return {"job_id": job_id, "is_favorite": update.is_favorite}


@app.get("/stats")
def job_stats(_: bool = Depends(require_token)) -> dict:
    """Return filter options, collection freshness, and database readiness."""
    try:
        with open_jobs_db() as conn:
            companies = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT company FROM jobs WHERE archived_at IS NULL AND LOWER(source) = 'linkedin' ORDER BY company LIMIT 200"
                ).fetchall()
            ]
            role_families = [
                r[0]
                for r in conn.execute(
                    """
                    SELECT DISTINCT role_family FROM jobs
                    WHERE archived_at IS NULL AND LOWER(source) = 'linkedin'
                      AND role_family IS NOT NULL AND role_family != ''
                    ORDER BY role_family
                    """
                ).fetchall()
            ]
            query_groups = [
                {"key": r[0], "name": r[1]}
                for r in conn.execute(
                    """
                    SELECT DISTINCT cq.query_group_key,
                           COALESCE(qg.display_name, cq.query_group_key)
                    FROM job_query_matches jqm
                    INNER JOIN jobs j ON j.job_id = jqm.job_id
                    INNER JOIN collection_queries cq
                        ON cq.collection_query_id = jqm.collection_query_id
                    LEFT JOIN query_groups qg ON qg.query_group_key = cq.query_group_key
                    WHERE j.archived_at IS NULL AND LOWER(j.source) = 'linkedin'
                    ORDER BY 2
                    """
                ).fetchall()
            ]
        return {
            "companies_list": companies,
            "role_families_list": role_families,
            "query_groups_list": query_groups,
            "collection_freshness": compute_collection_freshness(_require_job_db()),
            "database": database_readiness(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build stats: {exc}") from exc


@app.get("/db-summary")
def db_summary(window: Literal["all", "7d", "30d"] = "all", _: bool = Depends(require_token)) -> dict:
    """Return the collected market and coverage shown in Intelligence."""
    try:
        return build_db_summary(_require_job_db(), window=window)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build database summary: {exc}") from exc


def _row_to_dict(row: sqlite3.Row) -> dict:
    result = {key: row[key] for key in row.keys()}
    if "postings_json" in result:
        result["postings"] = json.loads(result.pop("postings_json"))
    if "is_favorite" in result:
        result["is_favorite"] = bool(result["is_favorite"])
    if "is_flagged" in result:
        result["is_flagged"] = bool(result["is_flagged"])
    return result


def _read_log_tail(path: Path) -> str:
    if not path.exists():
        fallback = Path(__file__).resolve().parent / "logs" / path.name
        if fallback.exists():
            path = fallback
        else:
            fallback = Path("/app/logs") / path.name
            if fallback.exists():
                path = fallback
            else:
                return ""
    data = path.read_bytes()
    if len(data) <= APP_SETTINGS.max_log_bytes:
        text = data.decode(errors="replace")
    else:
        text = data[-APP_SETTINGS.max_log_bytes :].decode(errors="replace")
    return _clean_log_text(text)


def _clean_log_text(text: str) -> str:
    if not text:
        return text
    cleaned = ANSI_ESCAPE_RE.sub("", text)
    if "\r" not in cleaned:
        return cleaned
    # Preserve progress updates that use '\r' as inline redraw.
    return cleaned.replace("\r", "\n")


def _active_days_between(first_seen_at: object, last_seen_at: object) -> int | None:
    try:
        first = datetime.fromisoformat(str(first_seen_at).replace("Z", "+00:00")) if first_seen_at else None
        last = datetime.fromisoformat(str(last_seen_at).replace("Z", "+00:00")) if last_seen_at else None
    except Exception:
        return None

    if not first or not last:
        return None
    delta_days = (last.date() - first.date()).days
    return max(0, delta_days) + 1


def _observation_from_row(row: sqlite3.Row | None) -> dict:
    if row is None:
        return {
            "first_seen_at": None,
            "last_seen_at": None,
            "seen_count": 0,
            "active_days": None,
        }

    keys = set(row.keys())
    first_seen_at = row["first_seen_at"] if "first_seen_at" in keys else None
    last_seen_at = row["last_seen_at"] if "last_seen_at" in keys else None
    seen_count = row["seen_count"] if "seen_count" in keys else None
    if not first_seen_at:
        first_seen_at = row["scraped_at"] if "scraped_at" in keys else None
    if not last_seen_at:
        last_seen_at = row["scraped_at"] if "scraped_at" in keys else None
    normalized_seen_count = int(seen_count) if seen_count not in (None, "") else 1

    return {
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
        "seen_count": normalized_seen_count,
        "active_days": _active_days_between(first_seen_at, last_seen_at),
    }


def _job_row_to_dict(row: sqlite3.Row) -> dict:
    job = _row_to_dict(row)
    query_groups_csv = str(job.pop("query_groups_csv", "") or "")
    job["query_groups"] = [value for value in query_groups_csv.split(",") if value]
    job["observation"] = _observation_from_row(row)
    return job


def mount_frontend(app_instance: FastAPI, frontend_dist_dir: Path) -> None:
    """Mount built assets and an index-only SPA fallback when a build exists."""
    frontend_index = frontend_dist_dir / "index.html"
    if not frontend_index.is_file():
        return

    assets_dir = frontend_dist_dir / "assets"
    if assets_dir.is_dir():
        app_instance.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="frontend-assets",
        )

    @app_instance.get("/", include_in_schema=False)
    async def serve_frontend_root() -> FileResponse:
        return FileResponse(str(frontend_index))

    @app_instance.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend_spa(full_path: str) -> FileResponse:
        # Client-side routes all receive the SPA shell. Never resolve the
        # request path against the filesystem; decoded traversal segments in
        # ``full_path`` must not influence which local file is returned.
        return FileResponse(str(frontend_index))


mount_frontend(app, FRONTEND_DIST_DIR)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
