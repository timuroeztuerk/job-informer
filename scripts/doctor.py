#!/usr/bin/env python3
"""Verify that Docker is serving this checkout and its SQLite database."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DATA = (ROOT / "backend" / "data").resolve()
READY_URL = "http://127.0.0.1:8000/ready"


def command_output(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def host_path(path: Path) -> Path:
    """Normalize Docker Desktop's /host_mnt prefix to the macOS host path."""
    value = str(path)
    if value.startswith("/host_mnt/"):
        value = value[len("/host_mnt") :]
    return Path(value).resolve()


def load_readiness() -> dict:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            with urlopen(READY_URL, timeout=2) as response:
                return json.load(response)
        except (OSError, URLError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Readiness endpoint did not become available: {last_error}")


def host_job_counts() -> tuple[int, int]:
    db_path = EXPECTED_DATA / "jobs.db"
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        total, active = conn.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE WHEN archived_at IS NULL THEN 1 ELSE 0 END)
            FROM jobs
            """
        ).fetchone()
    return int(total or 0), int(active or 0)


def main() -> int:
    container_id = command_output("docker", "compose", "ps", "-q", "backend")
    if not container_id:
        print("doctor: backend container is not running", file=sys.stderr)
        return 1

    inspection = json.loads(command_output("docker", "inspect", container_id))[0]
    labels = inspection.get("Config", {}).get("Labels", {}) or {}
    working_dir = Path(labels.get("com.docker.compose.project.working_dir", "")).resolve()
    data_mount = next(
        (
            host_path(Path(mount["Source"]))
            for mount in inspection.get("Mounts", [])
            if mount.get("Destination") == "/app/data"
        ),
        None,
    )

    errors: list[str] = []
    if working_dir != ROOT:
        errors.append(f"container belongs to {working_dir}, expected {ROOT}")
    if data_mount != EXPECTED_DATA:
        errors.append(f"/app/data comes from {data_mount}, expected {EXPECTED_DATA}")

    readiness = load_readiness()
    host_total, host_active = host_job_counts()
    if readiness.get("status") != "ready":
        errors.append(f"API database is not ready: {readiness.get('error') or readiness.get('missing_tables')}")
    if int(readiness.get("total_jobs", -1)) != host_total:
        errors.append(
            f"API reports {readiness.get('total_jobs')} jobs, host database has {host_total}"
        )
    if int(readiness.get("active_jobs", -1)) != host_active:
        errors.append(
            f"API reports {readiness.get('active_jobs')} active jobs, host database has {host_active}"
        )

    if errors:
        for error in errors:
            print(f"doctor: ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"doctor: OK — {readiness['instance_name']}, {host_total} total jobs, "
        f"{host_active} active, mount {data_mount}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
