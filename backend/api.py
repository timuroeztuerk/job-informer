#!/usr/bin/env python3
"""
Minimal HTTP API so the Vue frontend can trigger scrapes and read jobs.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Ensure local src imports work when running `uvicorn api:app`
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from src.utils.database import JobDatabase  # noqa: E402
from src.utils.db_summary import build_db_summary  # noqa: E402


DEFAULT_DB = ROOT / "data" / "jobs.db"
DB_PATH = os.getenv("JOBS_DB_PATH", str(DEFAULT_DB))
API_TOKEN = os.getenv("API_TOKEN", "")
MAX_LOG_BYTES = 8000  # Avoid sending huge logs to the frontend

app = FastAPI(title="Job Informer API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

job_db = JobDatabase(DB_PATH)


def require_token(x_api_token: str | None = Header(default=None)):
    """Optional bearer: if API_TOKEN is set, enforce it via header."""
    if API_TOKEN and x_api_token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API token.")
    return True


CLI_MODES: dict[str, list[str]] = {
    "run-once": ["--run-once"],
    "purge": ["--purge"],
    "ai-purge": ["--ai-purge"],
    "reset-ai-purge": ["--reset-ai-purge"],
    "parse-descriptions": ["--parse-descriptions"],
    "get-descriptions": ["--get-descriptions"],
    "db-summary": ["--db-summary"],
    "test": ["--test"],
}

DEFAULT_MODE = "run-once"


class RunRequest(BaseModel):
    mode: Literal[
        "run-once",
        "purge",
        "ai-purge",
        "reset-ai-purge",
        "parse-descriptions",
        "get-descriptions",
        "db-summary",
        "test",
    ] = DEFAULT_MODE
    keywords: Optional[str] = None
    locations: Optional[str] = None
    time_range: Optional[str] = None


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


class RunStore:
    """Lightweight persistence for run history."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
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
                    time_range TEXT
                )
                """
            )
            # Backfill missing columns for existing installs
            columns = {row[1] for row in conn.execute("PRAGMA table_info(api_runs)")}
            if "mode" not in columns:
                conn.execute("ALTER TABLE api_runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'run-once'")
            conn.commit()

    def save_start(
        self,
        run_id: str,
        log_path: Path,
        mode: str,
        keywords: Optional[str],
        locations: Optional[str],
        time_range: Optional[str],
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO api_runs (
                    run_id, mode, status, return_code, log_path, started_at, finished_at, keywords, locations, time_range
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    mode,
                    "running",
                    None,
                    str(log_path),
                    datetime.utcnow().isoformat(),
                    None,
                    keywords,
                    locations,
                    time_range,
                ),
            )
            conn.commit()

    def update_status(
        self,
        run_id: str,
        status: str,
        return_code: Optional[int],
        finished_at: Optional[datetime],
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE api_runs
                SET status = ?, return_code = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (status, return_code, finished_at.isoformat() if finished_at else None, run_id),
            )
            conn.commit()

    def get(self, run_id: str) -> Optional[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM api_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        return {k: row[k] for k in row.keys()}

    def list_recent(self, limit: int = 20) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM api_runs ORDER BY datetime(started_at) DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{k: row[k] for k in row.keys()} for row in rows]


run_store = RunStore(DB_PATH)


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
    ):
        self.run_id = run_id
        self.process = process
        self.log_path = log_path
        self.mode = mode
        self.started_at = datetime.utcnow()
        self.finished_at: Optional[datetime] = None
        self.keywords = keywords
        self.locations = locations
        self.time_range = time_range
        run_store.save_start(run_id, log_path, mode, keywords, locations, time_range)

    def status(self) -> RunStatus:
        code = self.process.poll()
        status = "running"
        finished = None
        if code is not None:
            finished = self.finished_at or datetime.utcnow()
            self.finished_at = finished
            status = "succeeded" if code == 0 else "failed"
            run_store.update_status(self.run_id, status, code, finished)
        else:
            run_store.update_status(self.run_id, status, None, None)
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
        )


# In-memory run registry; cleared when the server restarts.
RUNS: dict[str, RunRecord] = {}


@app.post("/runs", response_model=RunStatus)
def start_run(payload: RunRequest, _: bool = Depends(require_token)) -> RunStatus:
    """
    Start `python main.py --run-once` in the background.
    Optional overrides: keywords, locations, time_range.
    """
    run_id = uuid.uuid4().hex
    log_path = ROOT / "logs" / f"api_run_{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    mode = payload.mode or DEFAULT_MODE
    if mode not in CLI_MODES:
        raise HTTPException(status_code=400, detail=f"Unsupported mode: {mode}")

    cmd = [sys.executable, str(ROOT / "main.py"), *CLI_MODES[mode]]
    if payload.keywords:
        cmd += ["--keywords", payload.keywords]
    if payload.locations:
        cmd += ["--locations", payload.locations]
    if payload.time_range:
        cmd += ["--time", payload.time_range]

    log_file = log_path.open("w")
    process = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    RUNS[run_id] = RunRecord(
        run_id,
        process,
        log_path,
        mode,
        keywords=payload.keywords,
        locations=payload.locations,
        time_range=payload.time_range,
    )
    return RUNS[run_id].status()


@app.get("/runs/{run_id}", response_model=RunStatus)
def get_run_status(run_id: str, _: bool = Depends(require_token)) -> RunStatus:
    """Return current status and recent log output for a run."""
    record = RUNS.get(run_id)
    if record:
        return record.status()

    stored = run_store.get(run_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Run not found.")

    finished = datetime.fromisoformat(stored["finished_at"]) if stored.get("finished_at") else None
    started = datetime.fromisoformat(stored["started_at"]) if stored.get("started_at") else datetime.utcnow()
    return RunStatus(
        run_id=stored["run_id"],
        mode=stored.get("mode") or DEFAULT_MODE,
        status=stored["status"],
        return_code=stored["return_code"],
        log_tail=_read_log_tail(Path(stored["log_path"])),
        started_at=started,
        finished_at=finished,
        keywords=stored.get("keywords"),
        locations=stored.get("locations"),
        time_range=stored.get("time_range"),
    )


@app.get("/runs", response_model=list[RunSummary])
def list_runs(limit: int = Query(20, ge=1, le=100), _: bool = Depends(require_token)) -> list[RunSummary]:
    """Return recent runs (newest first)."""
    # Refresh statuses for in-memory tracked runs
    for record in list(RUNS.values()):
        record.status()
    records = run_store.list_recent(limit=limit)
    summaries: list[RunSummary] = []
    for record in records:
        summaries.append(
            RunSummary(
                run_id=record["run_id"],
                mode=record.get("mode") or DEFAULT_MODE,
                status=record["status"],
                return_code=record["return_code"],
                started_at=datetime.fromisoformat(record["started_at"]),
                finished_at=datetime.fromisoformat(record["finished_at"]) if record.get("finished_at") else None,
                keywords=record.get("keywords"),
                locations=record.get("locations"),
                time_range=record.get("time_range"),
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
    date_from: Optional[str] = Query(default=None, description="ISO date/time lower bound"),
    date_to: Optional[str] = Query(default=None, description="ISO date/time upper bound"),
    sort: str = Query(default="scraped_at_desc", description="scraped_at_desc|scraped_at_asc|title_asc|title_desc"),
    _: bool = Depends(require_token),
) -> dict:
    """Return a simple paginated job list with optional filters."""
    clauses = ["1=1"]
    params: list = []

    if search:
        like = f"%{search}%"
        clauses.append("(title LIKE ? OR company LIKE ? OR location LIKE ?)")
        params.extend([like, like, like])
    if location:
        clauses.append("location LIKE ?")
        params.append(f"%{location}%")
    if source:
        clauses.append("source = ?")
        params.append(source)
    if company:
        clauses.append("company LIKE ?")
        params.append(f"%{company}%")
    if date_from:
        clauses.append("datetime(COALESCE(scraped_at, created_at)) >= datetime(?)")
        params.append(date_from)
    if date_to:
        clauses.append("datetime(COALESCE(scraped_at, created_at)) <= datetime(?)")
        params.append(date_to)

    sort_map = {
        "scraped_at_desc": "datetime(COALESCE(scraped_at, created_at)) DESC",
        "scraped_at_asc": "datetime(COALESCE(scraped_at, created_at)) ASC",
        "title_asc": "title COLLATE NOCASE ASC",
        "title_desc": "title COLLATE NOCASE DESC",
    }
    order_by = sort_map.get(sort, sort_map["scraped_at_desc"])

    where_clause = " AND ".join(clauses)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            query = f"""
                SELECT job_id, title, company, location, source, url, salary, scraped_at
                FROM jobs
                WHERE {where_clause}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
            """
            rows = conn.execute(query, [*params, limit, offset]).fetchall()

            count_query = f"SELECT COUNT(*) FROM jobs WHERE {where_clause}"
            total = conn.execute(count_query, params).fetchone()[0]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    return {
        "total": total,
        "count": len(rows),
        "items": [_row_to_dict(row) for row in rows],
    }


@app.get("/jobs/{job_id:path}")
def get_job(job_id: str, _: bool = Depends(require_token)) -> dict:
    """Return a single job by id."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            parsed = _latest_parsed_description(conn, job_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _row_to_dict(row)
    if parsed:
        job["parsed_description"] = parsed
    return job


@app.delete("/jobs/{job_id:path}", status_code=204)
def delete_job(job_id: str, _: bool = Depends(require_token)) -> Response:
    """Delete a job and its parsed descriptions."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM parsed_descriptions WHERE job_id = ?", (job_id,))
            cursor.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Job not found")
            conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete job: {exc}") from exc
    return Response(status_code=204)


@app.get("/stats")
def job_stats(_: bool = Depends(require_token)) -> dict:
    """Lightweight wrapper around JobDatabase summary for dashboards."""
    try:
        summary = job_db.get_job_summary()
        with sqlite3.connect(DB_PATH) as conn:
            sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM jobs ORDER BY source").fetchall()]
            companies = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT company FROM jobs ORDER BY company LIMIT 200"
                ).fetchall()
            ]
        summary["sources_list"] = sources
        summary["companies_list"] = companies
        return summary
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build stats: {exc}") from exc


@app.get("/db-summary")
def db_summary(_: bool = Depends(require_token)) -> dict:
    """Expose the richer database summary that mirrors the CLI db-summary output."""
    try:
        return build_db_summary(job_db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build database summary: {exc}") from exc


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _read_log_tail(path: Path) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    if len(data) <= MAX_LOG_BYTES:
        return data.decode(errors="replace")
    return data[-MAX_LOG_BYTES:].decode(errors="replace")


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
