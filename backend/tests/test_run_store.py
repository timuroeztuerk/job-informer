"""Persistent API run-store tests."""

from __future__ import annotations

import os
import json
import time
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.api import RunStore, RunSummary
from backend.src.utils.run_progress import update_api_run_progress
from backend.src.utils.time_utils import as_utc_datetime


@contextmanager
def _temporary_timezone(name: str):
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


class TestRunStore(unittest.TestCase):
    def test_progress_writer_persists_structured_activity(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "runs.db"
            store = RunStore(str(db_path))
            store.claim_start("progress-run", Path(tmp_dir) / "run.log", "run-once", None, None, None)

            previous_run_id = os.environ.get("JOB_INFORMER_API_RUN_ID")
            previous_run_store = os.environ.get("RUN_STORE_DB_PATH")
            os.environ["JOB_INFORMER_API_RUN_ID"] = "progress-run"
            os.environ["RUN_STORE_DB_PATH"] = str(db_path)
            try:
                updated = update_api_run_progress(
                    stage="collecting",
                    label="Searching configured sources",
                    current_source="LinkedIn · Data in Berlin",
                    completed_sources=1,
                    total_sources=3,
                    metrics={"observed": 12, "new": 0, "archived": 0, "descriptions_fetched": 0, "parsed": 0},
                    event="Searching LinkedIn · Data in Berlin",
                )
            finally:
                if previous_run_id is None:
                    os.environ.pop("JOB_INFORMER_API_RUN_ID", None)
                else:
                    os.environ["JOB_INFORMER_API_RUN_ID"] = previous_run_id
                if previous_run_store is None:
                    os.environ.pop("RUN_STORE_DB_PATH", None)
                else:
                    os.environ["RUN_STORE_DB_PATH"] = previous_run_store

            self.assertTrue(updated)
            progress = json.loads(store.get("progress-run")["progress_json"])
            self.assertEqual(progress["stage"], "collecting")
            self.assertEqual(progress["current_source"], "LinkedIn · Data in Berlin")
            self.assertEqual(progress["completed_sources"], 1)
            self.assertEqual(progress["total_sources"], 3)
            self.assertEqual(progress["metrics"]["observed"], 12)
            self.assertEqual(progress["events"][-1]["message"], "Searching LinkedIn · Data in Berlin")

            store.update_status("progress-run", "succeeded", 0, datetime.now(UTC))
            completed_progress = json.loads(store.get("progress-run")["progress_json"])
            self.assertEqual(completed_progress["stage"], "completed")
            self.assertEqual(completed_progress["label"], "Run completed")

    def test_only_one_unfinished_run_can_be_claimed(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = RunStore(str(Path(tmp_dir) / "runs.db"))
            log_path = Path(tmp_dir) / "run.log"

            active = store.claim_start("first", log_path, "run-once", None, None, None)
            conflict = store.claim_start("second", log_path, "purge", None, None, None)

            self.assertIsNone(active)
            self.assertEqual(conflict["run_id"], "first")
            self.assertEqual(store.get("first")["status"], "starting")
            self.assertIsNone(store.get("second"))

    def test_startup_marks_unfinished_runs_interrupted(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = RunStore(str(Path(tmp_dir) / "runs.db"))
            log_path = Path(tmp_dir) / "run.log"
            store.claim_start("first", log_path, "run-once", None, None, None)
            store.mark_running("first", 12345)

            interrupted = store.interrupt_unfinished()
            record = store.get("first")

            self.assertEqual(interrupted, 1)
            self.assertEqual(record["status"], "interrupted")
            self.assertIsNone(record["pid"])
            self.assertIsNotNone(record["finished_at"])

    @unittest.skipUnless(hasattr(time, "tzset"), "requires POSIX timezone control")
    def test_run_timestamps_preserve_legacy_utc_and_serialize_unambiguously(self) -> None:
        with _temporary_timezone("Europe/Berlin"), TemporaryDirectory() as tmp_dir:
            store = RunStore(str(Path(tmp_dir) / "runs.db"))
            log_path = Path(tmp_dir) / "run.log"
            started_at = datetime(2026, 7, 12, 11, 0, tzinfo=UTC)
            store.claim_start(
                "new-aware",
                log_path,
                "run-once",
                None,
                None,
                None,
                started_at=started_at,
            )
            # RunStore historically received datetime.utcnow(), so a naive API
            # run value is UTC rather than Berlin local wall time.
            store.update_status(
                "new-aware",
                "succeeded",
                0,
                datetime(2026, 7, 12, 11, 5),
            )

            stored = store.get("new-aware")
            self.assertEqual(stored["started_at"], "2026-07-12T11:00:00+00:00")
            self.assertEqual(stored["finished_at"], "2026-07-12T11:05:00+00:00")

            api_payload = RunSummary(
                run_id=stored["run_id"],
                mode=stored["mode"],
                status=stored["status"],
                return_code=stored["return_code"],
                started_at=as_utc_datetime(stored["started_at"], naive_policy="utc"),
                finished_at=as_utc_datetime(stored["finished_at"], naive_policy="utc"),
            ).model_dump(mode="json")

            for key, expected_hour in (("started_at", 11), ("finished_at", 11)):
                serialized = api_payload[key]
                self.assertTrue(serialized.endswith("Z") or serialized.endswith("+00:00"))
                browser_instant = datetime.fromisoformat(serialized.replace("Z", "+00:00"))
                self.assertEqual(browser_instant.utcoffset(), timedelta(0))
                self.assertEqual(browser_instant.hour, expected_hour)

            legacy_utc = as_utc_datetime("2026-07-12T13:00:00", naive_policy="utc")
            self.assertEqual(legacy_utc, datetime(2026, 7, 12, 13, 0, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
