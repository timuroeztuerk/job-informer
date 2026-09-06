"""Persistent AI work, source snapshots, and immutable results."""

import json
import sqlite3
import time

from .prompt import SCHEMA_VERSION, contract, digest, encode
from ..utils.sqlite_connection import open_sqlite
from ..utils.time_utils import utc_now_iso


BATCH_SIZE = 100

# Eligibility follows the same consolidated role as the review UI. An old,
# archived posting may be the canonical ID of a role with an active posting.
ACTIVE_JOB_IDS = """SELECT m.job_id FROM job_memberships m
    JOIN review_jobs j ON j.job_id=m.canonical_job_id
    WHERE j.archived_at IS NULL
      AND COALESCE(j.relevance_outcome,'') NOT IN ('excluded','unrelated','manual_archive','auto_archived')"""
INACTIVE_MESSAGE = "AI extraction skipped because this job is filtered or archived. Restore the job before extracting again."

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_queue_state (
    id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0,
    reason TEXT, cooldown_until REAL NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO ai_queue_state(id) VALUES(1);
CREATE TABLE IF NOT EXISTS ai_work (
    work_id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
    source_id INTEGER NOT NULL REFERENCES description_sources(source_id),
    fingerprint TEXT NOT NULL UNIQUE, input_json TEXT NOT NULL, contract_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','retry_wait','succeeded','failed','interrupted')),
    requested_at TEXT NOT NULL, finished_at TEXT, attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0, error_kind TEXT, error_message TEXT,
    extraction_id INTEGER REFERENCES description_extractions(extraction_id)
);
CREATE TABLE IF NOT EXISTS ai_attempts (
    attempt_id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL REFERENCES ai_work(work_id),
    started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
    request_id TEXT, response_id TEXT, returned_model TEXT, service_tier TEXT,
    usage_json TEXT, latency_ms INTEGER, estimated_cost_usd REAL, error_kind TEXT,
    output_json TEXT, error_message TEXT
);
CREATE INDEX IF NOT EXISTS ai_pending ON ai_work(status,next_attempt_at);
"""


def initialize_schema(conn):
    conn.executescript(SCHEMA)
    if "estimated_cost_usd" not in {row[1] for row in conn.execute("PRAGMA table_info(ai_attempts)")}:
        conn.execute("ALTER TABLE ai_attempts ADD COLUMN estimated_cost_usd REAL")
    for column in ("output_json", "error_message"):
        if column not in {row[1] for row in conn.execute("PRAGMA table_info(ai_attempts)")}:
            conn.execute(f"ALTER TABLE ai_attempts ADD COLUMN {column} TEXT")


class AIStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def connect(self):
        return open_sqlite(self.db_path, row_factory=sqlite3.Row)

    @staticmethod
    def is_active(conn, job_id):
        return conn.execute(f"SELECT 1 WHERE ? IN ({ACTIVE_JOB_IDS})", (job_id,)).fetchone() is not None

    @staticmethod
    def _skip_inactive(conn):
        # Keep the work and its history, but never claim it again automatically.
        conn.execute(f"""UPDATE ai_work SET status='interrupted',finished_at=?,
            next_attempt_at=0,error_kind='inactive',error_message=?
            WHERE status IN ('queued','retry_wait') AND job_id NOT IN ({ACTIVE_JOB_IDS})""",
            (utc_now_iso(), INACTIVE_MESSAGE))

    def can_process(self, work):
        """Recheck eligibility after claiming, immediately before the paid request."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            attempt = conn.execute("""SELECT 1 FROM ai_attempts a JOIN ai_work w USING(work_id)
                WHERE a.attempt_id=? AND w.work_id=? AND a.status='running' AND w.status='running'""",
                (work["attempt_id"], work["work_id"])).fetchone()
            if attempt is None:
                return False
            if self.is_active(conn, work["job_id"]):
                return True
            now = utc_now_iso()
            conn.execute("""UPDATE ai_work SET status='interrupted',finished_at=?,next_attempt_at=0,
                error_kind='inactive',error_message=? WHERE work_id=?""", (now, INACTIVE_MESSAGE, work["work_id"]))
            conn.execute("""UPDATE ai_attempts SET status='interrupted',finished_at=?,error_kind='inactive',
                error_message=? WHERE attempt_id=?""", (now, INACTIVE_MESSAGE, work["attempt_id"]))
            return False

    @staticmethod
    def prepare(conn, job_id):
        row = conn.execute("""SELECT s.source_id, s.job_id, s.source_url, s.fetched_at,
            e.extractor, e.data_json, j.title, j.company, j.location
            FROM description_sources s JOIN description_extractions e USING(source_id)
            JOIN jobs j ON j.job_id=s.job_id
            WHERE s.job_id IN (SELECT job_id FROM job_memberships WHERE canonical_job_id=
                (SELECT canonical_job_id FROM job_memberships WHERE job_id=?))
                AND e.extractor IN ('linkedin_public_job','legacy_job_text')
            ORDER BY (e.extractor='legacy_job_text'), s.fetched_at DESC, s.source_id DESC, e.extraction_id DESC
            LIMIT 1""", (job_id,)).fetchone()
        if row is None:
            return None
        data = json.loads(row["data_json"])
        description = data.get("description_text", "")
        if len(description.strip()) < 80 or description.strip() == "FETCH_FAILED":
            return None
        # Full text is retained. Refuse oversized input explicitly, never truncate.
        if len(description.encode()) > 500_000:
            raise ValueError("Saved description is too large for this extractor; no text was truncated.")
        sources = {"title": data.get("title") or row["title"] or "",
                   "company": row["company"] or "", "location": row["location"] or ""}
        sources.update({f"description.{i}": line for i, line in enumerate(description.splitlines(), 1) if line.strip()})
        criteria = data.get("criteria", [])
        for i, item in enumerate(criteria):
            sources[f"criteria.{i}"] = f"{item['label']}: {item['value']}"
        input_data = {"sources": sources, "criteria": criteria,
                      "source_quality": "legacy_completeness_unknown" if row["extractor"] == "legacy_job_text" else "public_page"}
        return {"source_id": row["source_id"], "source_job_id": row["job_id"], "source_url": row["source_url"],
                "fetched_at": row["fetched_at"], "input": input_data,
                "fingerprint": digest({"input": input_data, "contract": contract()})}

    def enqueue(self, selected_job_id=None):
        added = 0
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if selected_job_id is not None:
                selected = conn.execute("SELECT canonical_job_id FROM job_memberships WHERE job_id=?", (selected_job_id,)).fetchone()
                if selected is None:
                    raise LookupError("Job not found.")
                selected_job_id = selected[0]
                if not self.is_active(conn, selected_job_id):
                    raise ValueError("Only active jobs can use AI extraction. Restore this job first.")
                if self.prepare(conn, selected_job_id) is None:
                    raise ValueError("Save a usable description for this job before extracting AI information.")
            candidates = [selected_job_id] if selected_job_id is not None else [r[0] for r in conn.execute(f"""SELECT j.job_id FROM review_jobs j
                WHERE LOWER(j.source)='linkedin' AND j.job_id IN ({ACTIVE_JOB_IDS})
                  AND EXISTS (SELECT 1 FROM saved_job_descriptions s WHERE s.job_id=j.job_id)
                ORDER BY j.is_favorite DESC, j.last_seen_at DESC, j.job_id""")]
            for job_id in candidates:
                prepared = self.prepare(conn, job_id)
                if prepared is None:
                    continue
                added += conn.execute("""INSERT OR IGNORE INTO ai_work
                    (job_id,source_id,fingerprint,input_json,contract_json,status,requested_at)
                    VALUES (?,?,?,?,?,'queued',?)""", (job_id, prepared["source_id"], prepared["fingerprint"],
                    # Preserve paragraph order for the model; only fingerprint serialization sorts keys.
                    json.dumps(prepared["input"], ensure_ascii=False, separators=(",", ":")),
                    encode(contract()), utc_now_iso())).rowcount
                if selected_job_id is not None:
                    # An explicit single-job retry must not restart other failed or completed work.
                    added += conn.execute("""UPDATE ai_work SET status='queued', attempts=0, next_attempt_at=0,
                        finished_at=NULL, error_kind=NULL, error_message=NULL WHERE fingerprint=?
                        AND status IN ('failed','interrupted')""", (prepared["fingerprint"],)).rowcount
                # Count new work, not candidates: cached or queued jobs do not consume the batch.
                if added >= BATCH_SIZE:
                    break
        return added

    def recover(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE ai_work SET status='interrupted', error_kind='interrupted', error_message='Interrupted before completion. Retry when ready.' WHERE status='running'")
            conn.execute("UPDATE ai_attempts SET status='interrupted', finished_at=?, error_kind='interrupted' WHERE status='running'", (utc_now_iso(),))
            self._skip_inactive(conn)
            if conn.execute(f"SELECT 1 FROM ai_work WHERE status IN ('queued','retry_wait','interrupted') AND job_id IN ({ACTIVE_JOB_IDS}) LIMIT 1").fetchone():
                conn.execute("UPDATE ai_queue_state SET paused=1, reason='Resume saved AI work when ready.' WHERE id=1")

    def pause(self, reason="AI extraction paused."):
        with self.connect() as conn:
            conn.execute("UPDATE ai_queue_state SET paused=1, reason=? WHERE id=1", (reason,))

    def resume(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._skip_inactive(conn)
            conn.execute("UPDATE ai_queue_state SET paused=0, reason=NULL WHERE id=1")

    def retry_failed(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Attempt history stays immutable; this starts a new bounded retry cycle.
            return conn.execute(f"""UPDATE ai_work SET status='queued', attempts=0, next_attempt_at=0,
                finished_at=NULL,error_kind=NULL,error_message=NULL WHERE status IN ('failed','interrupted')
                AND work_id IN (SELECT work_id FROM ai_work WHERE status IN ('failed','interrupted')
                    AND job_id IN ({ACTIVE_JOB_IDS})
                    AND work_id IN (SELECT MAX(work_id) FROM ai_work GROUP BY job_id)
                    ORDER BY work_id LIMIT ?)""", (BATCH_SIZE,)).rowcount

    def claim(self, limit):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._skip_inactive(conn)
            state = conn.execute("SELECT * FROM ai_queue_state WHERE id=1").fetchone()
            if state["paused"] or state["cooldown_until"] > time.time():
                return []
            # This database-wide count also enforces the ceiling at the claim boundary.
            running = conn.execute("SELECT COUNT(*) FROM ai_work WHERE status='running'").fetchone()[0]
            rows = conn.execute("""SELECT * FROM ai_work WHERE status IN ('queued','retry_wait')
                AND next_attempt_at<=? ORDER BY work_id LIMIT ?""", (time.time(), max(0, min(limit, 100-running)))).fetchall()
            work = []
            for row in rows:
                conn.execute("UPDATE ai_work SET status='running',attempts=attempts+1 WHERE work_id=?", (row["work_id"],))
                attempt_id = conn.execute("INSERT INTO ai_attempts(work_id,started_at,status) VALUES (?,?,'running')",
                                          (row["work_id"], utc_now_iso())).lastrowid
                work.append({**dict(row), "attempt_id": attempt_id, "attempts": row["attempts"]+1})
            return work

    def finish(self, work, *, payload=None, metadata=None, error_kind=None, message=None, retry_delay=None, global_backoff=False, draft=None):
        metadata = metadata or {}
        status = "succeeded" if payload is not None else "retry_wait" if retry_delay is not None else "failed"
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Late/cancelled attempts cannot overwrite a newer result.
            attempt = conn.execute("SELECT status FROM ai_attempts WHERE attempt_id=?", (work["attempt_id"],)).fetchone()
            if not attempt or attempt[0] != "running":
                return
            if not self.is_active(conn, work["job_id"]):
                # A request already sent cannot be unsent. Retain its usage, but
                # publish no new extraction and schedule no retry for this job.
                status, payload, retry_delay, global_backoff = "interrupted", None, None, False
                error_kind, message = "inactive", INACTIVE_MESSAGE
            extraction_id = None
            if payload is not None:
                document = {"fields": payload, "contract": json.loads(work["contract_json"]), "metadata": metadata,
                            "validation": {"schema": True, "evidence": True, "human_reviewed": False}}
                conn.execute("""INSERT OR IGNORE INTO description_extractions
                    (source_id,extractor,extractor_version,schema_version,extracted_at,content_sha256,data_json)
                    VALUES (?,'openai_job',?,?,?,?,?)""", (work["source_id"], work["fingerprint"], SCHEMA_VERSION,
                    now, digest(document), encode(document)))
                extraction_id = conn.execute("SELECT extraction_id FROM description_extractions WHERE source_id=? AND extractor='openai_job' AND extractor_version=? AND schema_version=?",
                                             (work["source_id"], work["fingerprint"], SCHEMA_VERSION)).fetchone()[0]
            conn.execute("""UPDATE ai_work SET status=?,finished_at=?,next_attempt_at=?,error_kind=?,error_message=?,
                extraction_id=COALESCE(?,extraction_id) WHERE work_id=?""", (status, now,
                time.time()+(retry_delay or 0), error_kind, message, extraction_id, work["work_id"]))
            conn.execute("""UPDATE ai_attempts SET status=?,finished_at=?,request_id=?,response_id=?,returned_model=?,
                service_tier=?,usage_json=?,latency_ms=?,estimated_cost_usd=?,error_kind=?,output_json=?,error_message=? WHERE attempt_id=?""", (status, now,
                metadata.get("request_id"), metadata.get("response_id"), metadata.get("model"), metadata.get("service_tier"),
                encode(metadata.get("usage", {})), metadata.get("latency_ms"), metadata.get("estimated_cost_usd"), error_kind,
                encode(draft) if draft is not None else None, message, work["attempt_id"]))
            if global_backoff and retry_delay is not None:
                conn.execute("UPDATE ai_queue_state SET cooldown_until=MAX(cooldown_until,?),reason=? WHERE id=1",
                             (time.time()+retry_delay, message))

    def status(self):
        with self.connect() as conn:
            state = dict(conn.execute("SELECT paused,reason,cooldown_until FROM ai_queue_state WHERE id=1").fetchone())
            counts = dict(conn.execute(f"""SELECT status,COUNT(*) FROM ai_work
                WHERE job_id IN ({ACTIVE_JOB_IDS})
                  AND work_id IN (SELECT MAX(work_id) FROM ai_work GROUP BY job_id) GROUP BY status"""))
            jobs = [dict(r) for r in conn.execute(f"""SELECT j.job_id,j.title,j.company,w.status
                FROM ai_work w JOIN jobs j USING(job_id)
                WHERE w.job_id IN ({ACTIVE_JOB_IDS})
                  AND w.work_id IN (SELECT MAX(work_id) FROM ai_work GROUP BY job_id)
                ORDER BY COALESCE(w.finished_at,w.requested_at) DESC,w.work_id DESC LIMIT 20""")]
            usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0}
            cost = conn.execute("SELECT COALESCE(SUM(estimated_cost_usd),0) FROM ai_attempts").fetchone()[0]
            for row in conn.execute("SELECT usage_json FROM ai_attempts WHERE usage_json IS NOT NULL"):
                value = json.loads(row[0])
                for key in usage:
                    usage[key] += int(value.get(key) or 0)
        return {**state, "paused": bool(state["paused"]), "batch_size": BATCH_SIZE, "jobs": jobs,
                "counts": counts, "usage": usage, "estimated_cost_usd": cost, "model": contract()["model"], "reasoning": "medium", "service_tier": "flex"}

    def job_result(self, job_id):
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone():
                raise LookupError("Job not found.")
            prepared = self.prepare(conn, job_id)
            members = [r[0] for r in conn.execute("SELECT job_id FROM job_memberships WHERE canonical_job_id=(SELECT canonical_job_id FROM job_memberships WHERE job_id=?)", (job_id,))]
            work = conn.execute("""SELECT w.*, e.data_json, e.extracted_at FROM ai_work w LEFT JOIN description_extractions e USING(extraction_id)
                WHERE w.fingerprint=? OR w.job_id IN (SELECT value FROM json_each(?))
                ORDER BY (w.fingerprint=?) DESC, (w.extraction_id IS NOT NULL) DESC, w.work_id DESC LIMIT 1""",
                (prepared["fingerprint"] if prepared else "", encode(members), prepared["fingerprint"] if prepared else "")).fetchone()
            saved_work = work if work and work["data_json"] else conn.execute("""SELECT w.*,e.data_json,e.extracted_at
                FROM ai_work w JOIN description_extractions e USING(extraction_id)
                WHERE w.job_id IN (SELECT value FROM json_each(?)) ORDER BY w.work_id DESC LIMIT 1""", (encode(members),)).fetchone()
            source = None
            if saved_work:
                source = dict(conn.execute("SELECT source_id,job_id,source_url,fetched_at FROM description_sources WHERE source_id=?", (saved_work["source_id"],)).fetchone())
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            legacy = review = None
            if "parsed_descriptions" in tables:
                columns = {r[1] for r in conn.execute("PRAGMA table_info(parsed_descriptions)")}
                if {"job_id", "payload_json", "model", "version", "created_at"} <= columns:
                    row = conn.execute("""SELECT model,version,created_at,payload_json FROM parsed_descriptions
                        WHERE job_id IN (SELECT value FROM json_each(?)) AND json_valid(payload_json)
                        AND COALESCE(json_extract(payload_json,'$.dry_run'),0)!=1 ORDER BY created_at DESC LIMIT 1""", (encode(members),)).fetchone()
                    if row:
                        legacy = dict(row)
                        legacy["fields"] = json.loads(legacy.pop("payload_json"))
            if "job_annotations" in tables:
                row = conn.execute("SELECT status,priority FROM job_annotations WHERE job_id IN (SELECT value FROM json_each(?)) ORDER BY updated_at DESC LIMIT 1", (encode(members),)).fetchone()
                review = dict(row) if row else None
            queue = dict(conn.execute("SELECT paused,cooldown_until FROM ai_queue_state WHERE id=1").fetchone())
            rejected = conn.execute("SELECT output_json FROM ai_attempts WHERE work_id=? ORDER BY attempt_id DESC LIMIT 1", (work["work_id"],)).fetchone() if work and work["status"] == "failed" else None
        saved = {**json.loads(saved_work["data_json"]), "extracted_at": saved_work["extracted_at"]} if saved_work else None
        input_data = json.loads(saved_work["input_json"]) if saved_work else prepared["input"] if prepared else None
        return {"job_id": job_id, "saved": saved, "input": input_data, "source": source,
                "status": work["status"] if work else None, "error": work["error_message"] if work else None,
                "stale": bool(saved_work and (not prepared or saved_work["fingerprint"] != prepared["fingerprint"])),
                "legacy": legacy, "legacy_review": review, "queue": queue,
                "rejected": json.loads(rejected[0]) if rejected and rejected[0] else None}
