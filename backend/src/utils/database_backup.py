"""Verified SQLite backup and restore helpers for the local archive."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def verify_sqlite_database(path: str | Path) -> None:
    """Raise when a database cannot be opened or fails an integrity check."""
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {candidate}")
    with sqlite3.connect(f"file:{candidate}?mode=ro", uri=True) as conn:
        result = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    if result.lower() != "ok":
        raise ValueError(f"SQLite integrity check failed for {candidate}: {result}")


def create_database_backup(
    database_path: str | Path,
    *,
    backup_dir: str | Path | None = None,
    label: str = "backup",
) -> Path:
    """Create and verify a consistent backup using SQLite's online API."""
    source_path = Path(database_path).expanduser().resolve()
    verify_sqlite_database(source_path)
    destination_dir = (
        Path(backup_dir).expanduser().resolve()
        if backup_dir is not None
        else source_path.parent / "backups"
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_label = "".join(character if character.isalnum() or character in "-_" else "-" for character in label)
    destination = destination_dir / f"{source_path.stem}-{safe_label}-{timestamp}.db"
    suffix = 1
    while destination.exists():
        destination = destination_dir / f"{source_path.stem}-{safe_label}-{timestamp}-{suffix}.db"
        suffix += 1

    with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)
    verify_sqlite_database(destination)
    return destination


def restore_database_backup(
    backup_path: str | Path,
    database_path: str | Path,
    *,
    create_safety_backup: bool = True,
) -> Path | None:
    """Verify and restore a backup, returning the pre-restore safety backup."""
    source_path = Path(backup_path).expanduser().resolve()
    target_path = Path(database_path).expanduser().resolve()
    if source_path == target_path:
        raise ValueError("Backup and target database paths must be different")
    verify_sqlite_database(source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    safety_backup: Path | None = None
    if target_path.exists() and create_safety_backup:
        safety_backup = create_database_backup(target_path, label="pre-restore")

    temporary_path = target_path.with_name(f".{target_path.name}.restore.tmp")
    if temporary_path.exists():
        temporary_path.unlink()
    try:
        with sqlite3.connect(source_path) as source, sqlite3.connect(temporary_path) as target:
            source.backup(target)
        verify_sqlite_database(temporary_path)
        os.replace(temporary_path, target_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    verify_sqlite_database(target_path)
    return safety_backup
