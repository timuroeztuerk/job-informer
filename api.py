#!/usr/bin/env python3
"""
Minimal HTTP API so the Vue frontend can trigger scrapes and read jobs.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Ensure local src imports work when running `uvicorn api:app`
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from src.utils.database import JobDatabase  # noqa: E402


DEFAULT_DB = ROOT / "data" / "jobs.db"
DB_PATH = os.getenv("JOBS_DB_PATH", str(DEFAULT_DB))
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


class RunRequest(BaseModel):
    keywords: Optional[str] = None
    locations: Optional[str] = None
    time_range: Optional[str] = None


class RunStatus(BaseModel):
    run_id: str
    status: str
    return_code: Optional[int] = None
    log_tail: str = ""
    started_at: datetime
    finished_at: Optional[datetime] = None


class RunRecord:
    """Holds lightweight state for a background run."""

    def __init__(self, run_id: str, process: subprocess.Popen, log_path: Path):
        self.run_id = run_id
        self.process = process
        self.log_path = log_path
        self.started_at = datetime.utcnow()
        self.finished_at: Optional[datetime] = None

    def status(self) -> RunStatus:
        code = self.process.poll()
        status = "running"
        finished = None
        if code is not None:
            finished = self.finished_at or datetime.utcnow()
            self.finished_at = finished
            status = "succeeded" if code == 0 else "failed"
        return RunStatus(
            run_id=self.run_id,
            status=status,
            return_code=code,
            log_tail=_read_log_tail(self.log_path),
            started_at=self.started_at,
            finished_at=finished,
        )


# In-memory run registry; cleared when the server restarts.
RUNS: dict[str, RunRecord] = {}


@app.post("/runs", response_model=RunStatus)
def start_run(payload: RunRequest) -> RunStatus:
    """
    Start `python main.py --run-once` in the background.
    Optional overrides: keywords, locations, time_range.
    """
    run_id = uuid.uuid4().hex
    log_path = ROOT / "logs" / f"api_run_{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, str(ROOT / "main.py"), "--run-once"]
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
    RUNS[run_id] = RunRecord(run_id, process, log_path)
    return RUNS[run_id].status()


@app.get("/runs/{run_id}", response_model=RunStatus)
def get_run_status(run_id: str) -> RunStatus:
    """Return current status and recent log output for a run."""
    record = RUNS.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found (server restarts clear history).")
    return record.status()


@app.get("/jobs")
def list_jobs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    location: Optional[str] = None,
    source: Optional[str] = None,
) -> dict:
    """Return a simple paginated job list with optional filters."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT job_id, title, company, location, source, url, salary, scraped_at FROM jobs WHERE 1=1"
            params: list = []

            if search:
                like = f"%{search}%"
                query += " AND (title LIKE ? OR company LIKE ? OR location LIKE ?)"
                params.extend([like, like, like])
            if location:
                query += " AND location LIKE ?"
                params.append(f"%{location}%")
            if source:
                query += " AND source = ?"
                params.append(source)

            query += " ORDER BY datetime(COALESCE(scraped_at, created_at)) DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    return {
        "total": total,
        "count": len(rows),
        "items": [_row_to_dict(row) for row in rows],
    }


@app.get("/jobs/{job_id:path}")
def get_job(job_id: str) -> dict:
    """Return a single job by id."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return _row_to_dict(row)


@app.get("/stats")
def job_stats() -> dict:
    """Lightweight wrapper around JobDatabase summary for dashboards."""
    try:
        return job_db.get_job_summary()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build stats: {exc}") from exc


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _read_log_tail(path: Path) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    if len(data) <= MAX_LOG_BYTES:
        return data.decode(errors="replace")
    return data[-MAX_LOG_BYTES:].decode(errors="replace")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
