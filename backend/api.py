#!/usr/bin/env python3
"""
Minimal HTTP API so the Vue frontend can trigger scrapes and read jobs.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import uuid
import re
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from src.utils.db_summary import build_db_summary, compute_collection_freshness  # noqa: E402
from src.utils.profile_fit import DEFAULT_FIT_PROFILE_ID, default_fit_profile  # noqa: E402
from src.utils.sqlite_connection import open_sqlite  # noqa: E402
from src.utils.time_utils import as_utc_datetime, normalize_utc_iso, utc_now, utc_now_iso  # noqa: E402


DEFAULT_DB = ROOT / "data" / "jobs.db"
ANSI_ESCAPE_RE = re.compile(r"(?:\x1B[@-Z\\-_]|\x1B\[[0-?]*[ -/]*[@-~])")


def _as_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _as_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
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
AUTO_COLLECTION_INTERVAL_HOURS = max(0.0, _as_float_env("AUTO_COLLECTION_INTERVAL_HOURS", 0.0))
APP_INSTANCE_NAME = os.getenv("APP_INSTANCE_NAME", "job-informer-local").strip() or "job-informer-local"

FRONTEND_DIST_DIR = ROOT / "frontend" / "dist"
REQUIRED_DB_TABLES = frozenset(
    {
        "filter_decisions",
        "fit_profiles",
        "job_annotations",
        "job_fit_scores",
        "job_observations",
        "jobs",
        "llm_attempts",
        "parse_job_states",
        "parsed_descriptions",
        "scrape_runs",
    }
)


@dataclass(frozen=True)
class AppSettings:
    db_path: str
    run_store_db_path: str
    api_token: str
    max_log_bytes: int
    frontend_dist_dir: Path
    auto_collection_interval_hours: float = 0.0
    instance_name: str = "job-informer-local"


APP_SETTINGS = AppSettings(
    db_path=DB_PATH,
    run_store_db_path=RUN_STORE_DB_PATH,
    api_token=API_TOKEN,
    max_log_bytes=MAX_LOG_BYTES,
    frontend_dist_dir=FRONTEND_DIST_DIR,
    auto_collection_interval_hours=AUTO_COLLECTION_INTERVAL_HOURS,
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
    global job_db, run_store, scheduler_task
    job_db = JobDatabase(APP_SETTINGS.db_path)
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
    scheduler_task = None
    if APP_SETTINGS.auto_collection_interval_hours > 0:
        scheduler_task = asyncio.create_task(_collection_scheduler())
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
        for record in list(RUNS.values()):
            if record.process.poll() is None:
                record.process.terminate()
                try:
                    await asyncio.to_thread(record.process.wait, 5)
                except subprocess.TimeoutExpired:
                    record.process.kill()
                run_store.update_status(record.run_id, "interrupted", None, utc_now())
        RUNS.clear()
        scheduler_task = None
        job_db = None
        run_store = None


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
scheduler_task: Optional[asyncio.Task] = None


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


CLI_MODES: dict[str, list[str]] = {
    "run-once": ["--run-once"],
    "purge": ["--purge"],
    "reset-ai-purge": ["--reset-ai-purge"],
    "parse-descriptions": ["--parse-descriptions"],
    "db-summary": ["--db-summary"],
    "test": ["--test"],
    "refetch-titles": ["--refetch-titles"],
}

DEFAULT_MODE = "run-once"
CANONICAL_MODES: dict[str, str] = {}
ANNOTATION_DEFAULT_STATUS = "unreviewed"
ANNOTATION_DEFAULT_PRIORITY = "medium"


def _canonical_mode(mode: str) -> str:
    return CANONICAL_MODES.get(mode, mode)


class RunRequest(BaseModel):
    mode: Literal[
        "run-once",
        "purge",
        "reset-ai-purge",
        "parse-descriptions",
        "db-summary",
        "test",
        "refetch-titles",
    ] = DEFAULT_MODE
    keywords: Optional[str] = None
    locations: Optional[str] = None
    time_range: Optional[str] = None


class RunProgressEvent(BaseModel):
    at: datetime
    level: Literal["info", "warning", "error"] = "info"
    message: str


class RunProgress(BaseModel):
    stage: str
    label: str
    current_source: Optional[str] = None
    completed_sources: Optional[int] = None
    total_sources: Optional[int] = None
    metrics: Optional[dict[str, int]] = None
    updated_at: datetime
    events: list[RunProgressEvent] = Field(default_factory=list)


class RunStatus(BaseModel):
    run_id: str
    mode: str
    status: str
    return_code: Optional[int] = None
    log_tail: str = ""
    started_at: datetime
    finished_at: Optional[datetime] = None
    keywords: Optional[str] = None
    locations: Optional[str] = None
    time_range: Optional[str] = None
    trigger: str = "manual"
    pid: Optional[int] = None
    metrics: Optional[dict[str, int]] = None
    progress: Optional[RunProgress] = None


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
    progress: Optional[RunProgress] = None


class JobAnnotationPayload(BaseModel):
    status: Literal[
        "unreviewed",
        "interesting",
        "applied",
        "interviewing",
        "offer",
        "rejected",
        "archived",
    ] = ANNOTATION_DEFAULT_STATUS
    priority: Literal["low", "medium", "high"] = ANNOTATION_DEFAULT_PRIORITY
    notes: str = ""
    why_interesting: str = ""
    skill_gaps: list[str] = Field(default_factory=list)
    follow_up_date: Optional[str] = None
    resume_version: str = ""


class JobAnnotationResponse(JobAnnotationPayload):
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class FitProfilePayload(BaseModel):
    name: str = default_fit_profile()["name"]
    config: dict[str, Any] = Field(default_factory=lambda: default_fit_profile()["config"])


class FitProfileResponse(FitProfilePayload):
    profile_id: str = DEFAULT_FIT_PROFILE_ID
    version: int = 1
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


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
            "current_source": None,
            "completed_sources": None,
            "total_sources": None,
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

    def latest_successful_collection(self) -> Optional[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM api_runs
                WHERE mode = 'run-once' AND status = 'succeeded'
                ORDER BY datetime(finished_at) DESC
                LIMIT 1
                """
            ).fetchone()
        return {key: row[key] for key in row.keys()} if row else None

    def latest_collection_attempt(self) -> Optional[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM api_runs
                WHERE mode = 'run-once'
                ORDER BY datetime(started_at) DESC
                LIMIT 1
                """
            ).fetchone()
        return {key: row[key] for key in row.keys()} if row else None


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
                    COALESCE(SUM(descriptions_fetched_count), 0),
                    COALESCE(SUM(parsed_jobs_count), 0),
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
        if not row or int(row[5] or 0) == 0:
            return None
        metrics = {
            "observed": int(row[0] or 0),
            "new": int(row[1] or 0),
            "archived": int(row[2] or 0),
            "descriptions_fetched": int(row[3] or 0),
            "parsed": int(row[4] or 0),
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
        return metrics
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
            progress=_progress_for_run(self._run_store.get(self.run_id) or {}),
        )


# In-memory run registry; cleared when the server restarts.
RUNS: dict[str, RunRecord] = {}


@app.post("/runs", response_model=RunStatus)
def start_run(payload: RunRequest, _: bool = Depends(require_token)) -> RunStatus:
    """
    Start `python main.py --run-once` in the background.
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

    requested_mode = payload.mode or DEFAULT_MODE
    if requested_mode not in CLI_MODES:
        raise HTTPException(status_code=400, detail=f"Unsupported mode: {requested_mode}")

    mode = _canonical_mode(requested_mode)
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

    cmd = [sys.executable, "-u", str(ROOT / "main.py"), *CLI_MODES[requested_mode]]
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


async def _collection_scheduler() -> None:
    """Launch the normal collection pipeline at an opt-in fixed interval."""
    interval = timedelta(hours=APP_SETTINGS.auto_collection_interval_hours)
    while True:
        latest = _require_run_store().latest_collection_attempt()
        reference_times: list[datetime] = []
        if latest and latest.get("started_at"):
            with suppress(ValueError):
                timestamp = as_utc_datetime(latest["started_at"], naive_policy="utc")
                if timestamp is not None:
                    reference_times.append(timestamp)
        freshness = compute_collection_freshness(_require_job_db())
        if freshness.get("last_collected_at"):
            with suppress(ValueError):
                timestamp = as_utc_datetime(freshness["last_collected_at"])
                if timestamp is not None:
                    reference_times.append(timestamp)
        last_activity = max(reference_times) if reference_times else None
        due = last_activity is None or datetime.now(UTC) - last_activity >= interval
        if due:
            try:
                _launch_run(RunRequest(mode="run-once"), trigger="scheduled")
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
        await asyncio.sleep(60)


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
        mode=_canonical_mode(stored.get("mode") or DEFAULT_MODE),
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
                mode=_canonical_mode(record.get("mode") or DEFAULT_MODE),
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
    fit_profile_id: str = Query(default=DEFAULT_FIT_PROFILE_ID),
    fit_band: Optional[str] = None,
    min_fit_score: Optional[float] = Query(default=None, ge=0, le=100),
    annotation_status: Optional[str] = None,
    annotation_priority: Optional[str] = None,
    archived: Literal["exclude", "include", "only"] = Query(default="exclude"),
    date_from: Optional[str] = Query(default=None, description="ISO date/time lower bound"),
    date_to: Optional[str] = Query(default=None, description="ISO date/time upper bound"),
    sort: str = Query(
        default="scraped_at_desc",
        description="scraped_at_desc|scraped_at_asc|last_seen_desc|last_seen_asc|seen_count_desc|seen_count_asc|fit_score_desc|fit_score_asc|title_asc|title_desc",
    ),
    _: bool = Depends(require_token),
) -> dict:
    """Return a simple paginated job list with optional filters."""
    clauses = ["1=1"]
    params: list[str] = []
    join_params: list[Any] = [fit_profile_id]

    if archived == "exclude":
        clauses.append("j.archived_at IS NULL")
    elif archived == "only":
        clauses.append("j.archived_at IS NOT NULL")

    if search:
        like = f"%{search}%"
        clauses.append("(j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ?)")
        params.extend([like, like, like])
    if location:
        clauses.append("j.location LIKE ?")
        params.append(f"%{location}%")
    if source:
        clauses.append("j.source = ?")
        params.append(source)
    if company:
        clauses.append("j.company LIKE ?")
        params.append(f"%{company}%")
    if fit_band:
        clauses.append("COALESCE(f.band, 'unscored') = ?")
        params.append(fit_band)
    if min_fit_score is not None:
        clauses.append("COALESCE(f.score, 0) >= ?")
        params.append(float(min_fit_score))
    if annotation_status:
        clauses.append(f"COALESCE(a.status, '{ANNOTATION_DEFAULT_STATUS}') = ?")
        params.append(annotation_status)
    if annotation_priority:
        clauses.append(f"COALESCE(a.priority, '{ANNOTATION_DEFAULT_PRIORITY}') = ?")
        params.append(annotation_priority)
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
        "fit_score_desc": "COALESCE(f.score, 0) DESC, datetime(COALESCE(j.last_seen_at, j.scraped_at, j.created_at)) DESC",
        "fit_score_asc": "COALESCE(f.score, 0) ASC, datetime(COALESCE(j.last_seen_at, j.scraped_at, j.created_at)) DESC",
        "title_asc": "j.title COLLATE NOCASE ASC",
        "title_desc": "j.title COLLATE NOCASE DESC",
    }
    order_by = sort_map.get(sort, sort_map["scraped_at_desc"])

    where_clause = " AND ".join(clauses)
    try:
        with open_jobs_db() as conn:
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
                    j.first_seen_at,
                    j.last_seen_at,
                    j.seen_count,
                    f.score AS fit_score,
                    f.band AS fit_band,
                    f.reasons_json AS fit_reasons_json,
                    f.signals_json AS fit_signals_json,
                    f.computed_at AS fit_computed_at,
                    f.profile_version AS fit_profile_version,
                    a.status AS annotation_status,
                    a.priority AS annotation_priority,
                    a.notes AS annotation_notes,
                    a.why_interesting AS annotation_why_interesting,
                    a.skill_gaps AS annotation_skill_gaps,
                    a.follow_up_date AS annotation_follow_up_date,
                    a.resume_version AS annotation_resume_version,
                    a.created_at AS annotation_created_at,
                    a.updated_at AS annotation_updated_at
                FROM jobs j
                LEFT JOIN job_fit_scores f ON f.job_id = j.job_id AND f.profile_id = ?
                LEFT JOIN job_annotations a ON a.job_id = j.job_id
                WHERE {where_clause}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
            """
            rows = conn.execute(query, [*join_params, *params, limit, offset]).fetchall()

            count_query = f"""
                SELECT COUNT(*)
                FROM jobs j
                LEFT JOIN job_fit_scores f ON f.job_id = j.job_id AND f.profile_id = ?
                LEFT JOIN job_annotations a ON a.job_id = j.job_id
                WHERE {where_clause}
            """
            total = conn.execute(count_query, [*join_params, *params]).fetchone()[0]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    return {
        "total": total,
        "count": len(rows),
        "items": [_job_row_to_dict(row) for row in rows],
    }


@app.get("/jobs/{job_id:path}")
def get_job(
    job_id: str,
    fit_profile_id: str = Query(default=DEFAULT_FIT_PROFILE_ID),
    _: bool = Depends(require_token),
) -> dict:
    """Return a single job by id."""
    try:
        with open_jobs_db() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            parsed = _latest_parsed_description(conn, job_id)
            annotation = _job_annotation_for_job(conn, job_id)
            observations = _job_observations_for_job(conn, job_id)
            fit = _job_fit_for_job(conn, job_id, fit_profile_id)
            filter_decisions = _require_job_db().get_filter_decisions(job_id)
            parse_status = _require_job_db().get_parse_status(job_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _row_to_dict(row)
    if parsed:
        job["parsed_description"] = parsed
    job["annotation"] = annotation
    job["observation"] = _observation_from_row(row)
    job["observations"] = observations
    job["fit"] = fit
    job["parse_status"] = parse_status
    job["filter_decisions"] = filter_decisions
    return job


@app.put("/jobs/{job_id:path}/annotation", response_model=JobAnnotationResponse)
def update_job_annotation(
    job_id: str,
    payload: JobAnnotationPayload,
    _: bool = Depends(require_token),
) -> JobAnnotationResponse:
    """Create or update personal workflow annotations for a job."""
    try:
        with open_jobs_db() as conn:
            existing = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Job not found")

            normalized_skill_gaps = _normalize_skill_gaps(payload.skill_gaps)
            updated_at = utc_now_iso()
            conn.execute(
                """
                INSERT INTO job_annotations (
                    job_id,
                    status,
                    priority,
                    notes,
                    why_interesting,
                    skill_gaps,
                    follow_up_date,
                    resume_version,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    priority = excluded.priority,
                    notes = excluded.notes,
                    why_interesting = excluded.why_interesting,
                    skill_gaps = excluded.skill_gaps,
                    follow_up_date = excluded.follow_up_date,
                    resume_version = excluded.resume_version,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    payload.status,
                    payload.priority,
                    payload.notes.strip(),
                    payload.why_interesting.strip(),
                    json.dumps(normalized_skill_gaps),
                    payload.follow_up_date or None,
                    payload.resume_version.strip(),
                    updated_at,
                    updated_at,
                ),
            )
            conn.commit()

            row = conn.execute(
                """
                SELECT
                    status,
                    priority,
                    notes,
                    why_interesting,
                    skill_gaps,
                    follow_up_date,
                    resume_version,
                    created_at,
                    updated_at
                FROM job_annotations
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update annotation: {exc}") from exc

    annotation = _annotation_from_row(row)
    return JobAnnotationResponse(**annotation)


@app.get("/fit-profile", response_model=FitProfileResponse)
def get_fit_profile(
    profile_id: str = Query(default=DEFAULT_FIT_PROFILE_ID),
    _: bool = Depends(require_token),
) -> FitProfileResponse:
    """Return the active profile-fit configuration."""
    profile = _require_job_db().get_fit_profile(profile_id)
    return FitProfileResponse(**profile)


@app.put("/fit-profile", response_model=FitProfileResponse)
def update_fit_profile(
    payload: FitProfilePayload,
    profile_id: str = Query(default=DEFAULT_FIT_PROFILE_ID),
    _: bool = Depends(require_token),
) -> FitProfileResponse:
    """Update the active profile-fit configuration and refresh scores."""
    db = _require_job_db()
    profile = db.upsert_fit_profile(profile_id=profile_id, name=payload.name, config=payload.config)
    db.recompute_fit_scores(profile_id=profile_id)
    return FitProfileResponse(**profile)


@app.post("/fit-profile/recompute")
def recompute_fit_profile(
    profile_id: str = Query(default=DEFAULT_FIT_PROFILE_ID),
    _: bool = Depends(require_token),
) -> dict:
    """Recompute persisted fit scores for the selected profile."""
    updated = _require_job_db().recompute_fit_scores(profile_id=profile_id)
    return {"profile_id": profile_id, "updated": updated}


@app.delete("/jobs/{job_id:path}", status_code=204)
def delete_job(job_id: str, _: bool = Depends(require_token)) -> Response:
    """Archive a job instead of hard-deleting it."""
    try:
        db = _require_job_db()
        with open_jobs_db() as conn:
            existing = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Job not found")

        archive_summary = db.archive_jobs_with_filter_decisions(
            [
                {
                    "job_id": job_id,
                    "decision_source": "manual",
                    "decision_action": "archive",
                    "filter_name": "manual_archive",
                    "reason": "Archived manually via API",
                }
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
            row = conn.execute("SELECT archived_at FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
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


@app.get("/stats")
def job_stats(_: bool = Depends(require_token)) -> dict:
    """Lightweight wrapper around JobDatabase summary for dashboards."""
    try:
        summary = _require_job_db().get_job_summary()
        with open_jobs_db() as conn:
            sources = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT source FROM jobs WHERE archived_at IS NULL ORDER BY source"
                ).fetchall()
            ]
            companies = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT company FROM jobs WHERE archived_at IS NULL ORDER BY company LIMIT 200"
                ).fetchall()
            ]
        summary["sources_list"] = sources
        summary["companies_list"] = companies
        summary["collection_freshness"] = compute_collection_freshness(_require_job_db())
        latest_success = _require_run_store().latest_successful_collection()
        summary["collection_scheduler"] = {
            "enabled": APP_SETTINGS.auto_collection_interval_hours > 0,
            "interval_hours": APP_SETTINGS.auto_collection_interval_hours,
            "last_successful_run_at": latest_success.get("finished_at") if latest_success else None,
        }
        summary["database"] = database_readiness()
        return summary
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build stats: {exc}") from exc


@app.get("/db-summary")
def db_summary(_: bool = Depends(require_token)) -> dict:
    """Expose the richer database summary that mirrors the CLI db-summary output."""
    try:
        return build_db_summary(_require_job_db())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build database summary: {exc}") from exc


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


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


def _normalize_skill_gaps(values: list[str] | None) -> list[str]:
    if not values:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(str(value).strip().split())
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(clean)
    return normalized


def _parse_skill_gaps(raw_value: object) -> list[str]:
    if raw_value in (None, ""):
        return []
    if isinstance(raw_value, list):
        return _normalize_skill_gaps([str(item) for item in raw_value])
    try:
        payload = json.loads(str(raw_value))
    except Exception:
        payload = [part.strip() for part in str(raw_value).split(",")]
    if isinstance(payload, list):
        return _normalize_skill_gaps([str(item) for item in payload])
    return _normalize_skill_gaps([str(payload)])


def _annotation_from_row(row: sqlite3.Row | None) -> dict:
    if row is None:
        return {
            "status": ANNOTATION_DEFAULT_STATUS,
            "priority": ANNOTATION_DEFAULT_PRIORITY,
            "notes": "",
            "why_interesting": "",
            "skill_gaps": [],
            "follow_up_date": None,
            "resume_version": "",
            "created_at": None,
            "updated_at": None,
        }

    keys = set(row.keys())
    status_key = "annotation_status" if "annotation_status" in keys else "status"
    priority_key = "annotation_priority" if "annotation_priority" in keys else "priority"
    notes_key = "annotation_notes" if "annotation_notes" in keys else "notes"
    why_key = "annotation_why_interesting" if "annotation_why_interesting" in keys else "why_interesting"
    gaps_key = "annotation_skill_gaps" if "annotation_skill_gaps" in keys else "skill_gaps"
    follow_up_key = "annotation_follow_up_date" if "annotation_follow_up_date" in keys else "follow_up_date"
    resume_key = "annotation_resume_version" if "annotation_resume_version" in keys else "resume_version"
    created_key = "annotation_created_at" if "annotation_created_at" in keys else "created_at"
    updated_key = "annotation_updated_at" if "annotation_updated_at" in keys else "updated_at"

    return {
        "status": row[status_key] or ANNOTATION_DEFAULT_STATUS,
        "priority": row[priority_key] or ANNOTATION_DEFAULT_PRIORITY,
        "notes": row[notes_key] or "",
        "why_interesting": row[why_key] or "",
        "skill_gaps": _parse_skill_gaps(row[gaps_key] if gaps_key in keys else None),
        "follow_up_date": row[follow_up_key] if follow_up_key in keys else None,
        "resume_version": row[resume_key] or "",
        "created_at": row[created_key] if created_key in keys else None,
        "updated_at": row[updated_key] if updated_key in keys else None,
    }


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


def _fit_from_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None

    keys = set(row.keys())
    if "fit_score" not in keys and "score" not in keys:
        return None

    score_key = "fit_score" if "fit_score" in keys else "score"
    band_key = "fit_band" if "fit_band" in keys else "band"
    reasons_key = "fit_reasons_json" if "fit_reasons_json" in keys else "reasons_json"
    signals_key = "fit_signals_json" if "fit_signals_json" in keys else "signals_json"
    computed_key = "fit_computed_at" if "fit_computed_at" in keys else "computed_at"
    profile_version_key = "fit_profile_version" if "fit_profile_version" in keys else "profile_version"
    profile_id_key = "fit_profile_id" if "fit_profile_id" in keys else "profile_id"

    score_value = row[score_key] if score_key in keys else None
    if score_value in (None, ""):
        return None

    try:
        reasons = json.loads(row[reasons_key]) if reasons_key in keys and row[reasons_key] else []
    except Exception:
        reasons = []
    try:
        signals = json.loads(row[signals_key]) if signals_key in keys and row[signals_key] else {}
    except Exception:
        signals = {}

    return {
        "profile_id": row[profile_id_key] if profile_id_key in keys else DEFAULT_FIT_PROFILE_ID,
        "score": float(score_value),
        "band": row[band_key] if band_key in keys else "unscored",
        "reasons": reasons if isinstance(reasons, list) else [],
        "signals": signals if isinstance(signals, dict) else {},
        "computed_at": row[computed_key] if computed_key in keys else None,
        "profile_version": int(row[profile_version_key] or 1) if profile_version_key in keys else 1,
    }


def _job_row_to_dict(row: sqlite3.Row) -> dict:
    job = _row_to_dict(row)
    for key in [
        "fit_score",
        "fit_band",
        "fit_reasons_json",
        "fit_signals_json",
        "fit_computed_at",
        "fit_profile_version",
        "annotation_status",
        "annotation_priority",
        "annotation_notes",
        "annotation_why_interesting",
        "annotation_skill_gaps",
        "annotation_follow_up_date",
        "annotation_resume_version",
        "annotation_created_at",
        "annotation_updated_at",
    ]:
        job.pop(key, None)
    job["annotation"] = _annotation_from_row(row)
    job["observation"] = _observation_from_row(row)
    job["fit"] = _fit_from_row(row)
    return job


def _job_annotation_for_job(conn: sqlite3.Connection, job_id: str) -> dict:
    row = conn.execute(
        """
        SELECT
            status,
            priority,
            notes,
            why_interesting,
            skill_gaps,
            follow_up_date,
            resume_version,
            created_at,
            updated_at
        FROM job_annotations
        WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()
    return _annotation_from_row(row)


def _job_observations_for_job(conn: sqlite3.Connection, job_id: str, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            observed_at,
            scrape_run_id,
            title,
            company,
            location,
            source,
            url,
            salary,
            description_present,
            is_new
        FROM job_observations
        WHERE job_id = ?
        ORDER BY datetime(observed_at) DESC, observation_id DESC
        LIMIT ?
        """,
        (job_id, max(1, int(limit))),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _job_fit_for_job(conn: sqlite3.Connection, job_id: str, profile_id: str = DEFAULT_FIT_PROFILE_ID) -> dict | None:
    row = conn.execute(
        """
        SELECT
            profile_id AS fit_profile_id,
            score AS fit_score,
            band AS fit_band,
            reasons_json AS fit_reasons_json,
            signals_json AS fit_signals_json,
            computed_at AS fit_computed_at,
            profile_version AS fit_profile_version
        FROM job_fit_scores
        WHERE profile_id = ? AND job_id = ?
        """,
        (profile_id, job_id),
    ).fetchone()
    return _fit_from_row(row)


def _latest_parsed_description(conn: sqlite3.Connection, job_id: str) -> dict | None:
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT payload_json, version, created_at
            FROM parsed_descriptions
            WHERE job_id = ?
            ORDER BY version DESC, datetime(created_at) DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
        if not row:
            return None
        payload = None
        try:
            payload = json.loads(row["payload_json"])
        except Exception:
            payload = None
        return {
            "payload": payload,
            "raw": row["payload_json"],
            "version": row["version"],
            "created_at": row["created_at"],
        }
    except Exception:
        return None


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
