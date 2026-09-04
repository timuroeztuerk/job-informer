#!/usr/bin/env python3
"""Explicit local diagnostics and maintenance commands."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


BACKEND_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_ROOT.parent
EXPECTED_DATA = (BACKEND_ROOT / "data").resolve()
READY_URL = "http://127.0.0.1:8000/ready"

os.chdir(BACKEND_ROOT)
sys.path.insert(0, str(BACKEND_ROOT / "src"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain the local Job Informer SQLite archive")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Verify the running Docker app and its SQLite database")

    backup = subparsers.add_parser("backup", help="Create and verify a SQLite backup")
    backup.add_argument("--output-dir", help="Optional backup directory")

    restore = subparsers.add_parser("restore", help="Restore a verified SQLite backup")
    restore.add_argument("backup_path")
    restore.add_argument("--confirm", action="store_true", help="Confirm replacement of the active database")

    preview = subparsers.add_parser("relevance-preview", help="Preview deterministic relevance outcomes")
    preview.add_argument("--include-unmatched", action="store_true")
    preview.add_argument("--all", action="store_true", help="Evaluate active and archived jobs")
    preview.add_argument(
        "--reconcile-archive",
        action="store_true",
        help="Preview restoring old automatic archives that the current policy keeps",
    )

    apply = subparsers.add_parser("relevance-apply", help="Back up and apply deterministic relevance outcomes")
    apply.add_argument("--include-unmatched", action="store_true")
    apply.add_argument("--all", action="store_true", help="Evaluate active and archived jobs")
    apply.add_argument(
        "--reconcile-archive",
        action="store_true",
        help="Restore old automatic archives that the current policy keeps",
    )
    apply.add_argument("--confirm", action="store_true", help="Confirm the soft-archive batch")
    return parser.parse_args()


def _command_output(*args: str) -> str:
    return subprocess.check_output(args, cwd=PROJECT_ROOT, text=True).strip()


def _host_path(path: Path) -> Path:
    """Normalize Docker Desktop's /host_mnt prefix to the macOS host path."""
    value = str(path)
    if value.startswith("/host_mnt/"):
        value = value[len("/host_mnt") :]
    return Path(value).resolve()


def _load_readiness() -> dict:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            with urlopen(READY_URL, timeout=2) as response:
                return json.load(response)
        except (OSError, URLError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Readiness endpoint did not become available: {last_error}")


def _host_job_counts() -> tuple[int, int]:
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


def _run_doctor() -> int:
    container_id = _command_output("docker", "compose", "ps", "-q", "backend")
    if not container_id:
        print("doctor: backend container is not running", file=sys.stderr)
        return 1

    inspection = json.loads(_command_output("docker", "inspect", container_id))[0]
    labels = inspection.get("Config", {}).get("Labels", {}) or {}
    working_dir = Path(labels.get("com.docker.compose.project.working_dir", "")).resolve()
    data_mount = next(
        (
            _host_path(Path(mount["Source"]))
            for mount in inspection.get("Mounts", [])
            if mount.get("Destination") == "/app/data"
        ),
        None,
    )

    errors: list[str] = []
    if working_dir != PROJECT_ROOT:
        errors.append(f"container belongs to {working_dir}, expected {PROJECT_ROOT}")
    if data_mount != EXPECTED_DATA:
        errors.append(f"/app/data comes from {data_mount}, expected {EXPECTED_DATA}")

    readiness = _load_readiness()
    host_total, host_active = _host_job_counts()
    if readiness.get("status") != "ready":
        errors.append(f"API database is not ready: {readiness.get('error') or readiness.get('missing_tables')}")
    if int(readiness.get("total_jobs", -1)) != host_total:
        errors.append(f"API reports {readiness.get('total_jobs')} jobs, host database has {host_total}")
    if int(readiness.get("active_jobs", -1)) != host_active:
        errors.append(f"API reports {readiness.get('active_jobs')} active jobs, host database has {host_active}")

    if errors:
        for error in errors:
            print(f"doctor: ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"doctor: OK — {readiness['instance_name']}, {host_total} total jobs, "
        f"{host_active} active, mount {data_mount}"
    )
    return 0


def _public_preview(preview: dict[str, object]) -> dict[str, object]:
    public = {
        key: value
        for key, value in preview.items()
        if key not in {"evaluations", "decisions", "conflicts"}
    }
    conflicts = list(preview.get("conflicts") or [])
    public["conflict_count"] = len(conflicts)
    public["conflicts"] = conflicts[:10]
    return public


def main() -> int:
    args = parse_args()

    if args.command == "doctor":
        return _run_doctor()

    from src.config.settings import Config
    from src.utils.database import JobDatabase
    from src.utils.database_backup import create_database_backup, restore_database_backup
    from src.utils.relevance_service import apply_relevance_preview, build_relevance_preview

    config = Config.from_env()
    db = JobDatabase(config.jobs_db_path)

    if args.command == "backup":
        backup_path = create_database_backup(db.db_path, backup_dir=args.output_dir)
        print(json.dumps({"status": "ok", "backup": str(backup_path)}, indent=2))
        return 0

    if args.command == "restore":
        if not args.confirm:
            raise SystemExit("Restore requires --confirm. Stop the app before replacing its database.")
        safety_backup = restore_database_backup(args.backup_path, db.db_path)
        print(
            json.dumps(
                {
                    "status": "ok",
                    "restored_from": str(Path(args.backup_path).expanduser().resolve()),
                    "safety_backup": str(safety_backup) if safety_backup else None,
                },
                indent=2,
            )
        )
        return 0

    if args.reconcile_archive and not args.all:
        raise SystemExit("--reconcile-archive requires --all")
    preview = build_relevance_preview(
        db,
        include_unmatched=args.include_unmatched,
        active_only=not args.all,
        reconcile_archive=args.reconcile_archive,
    )
    if args.command == "relevance-preview":
        print(json.dumps(_public_preview(preview), indent=2, ensure_ascii=False))
        return 0

    if not args.confirm:
        raise SystemExit("Relevance apply requires --confirm after reviewing relevance-preview.")
    backup_path = create_database_backup(db.db_path, label="pre-relevance")
    result = apply_relevance_preview(db, preview)
    with db._get_connection() as conn:  # noqa: SLF001
        foreign_key_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
        dataset = conn.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE WHEN archived_at IS NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN relevance_outcome IS NOT NULL THEN 1 ELSE 0 END)
            FROM jobs WHERE LOWER(source) = 'linkedin'
            """
        ).fetchone()
        filtered_count = int(conn.execute("SELECT COUNT(*) FROM filtered_jobs").fetchone()[0])
    if foreign_key_issues:
        raise RuntimeError(f"Foreign-key verification failed after apply: {foreign_key_issues[:5]}")
    dataset_summary = {
        "total": int(dataset[0] or 0),
        "active_filtered": int(dataset[1] or 0),
        "archived": int(dataset[2] or 0),
        "classified": int(dataset[3] or 0),
        "filtered_view_rows": filtered_count,
        "foreign_key_check": "ok",
    }
    print(
        json.dumps(
            {
                "status": "ok",
                "backup": str(backup_path),
                "preview": _public_preview(preview),
                "result": result,
                "dataset": dataset_summary,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
