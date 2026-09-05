"""Transactional fetch queue, immutable source versions, and extraction history."""

import hashlib
import json
import sqlite3

from .linkedin import EXTRACTOR, EXTRACTOR_VERSION, SCHEMA_VERSION, extraction_hash, source_url
from .legacy import LEGACY_EXTRACTOR
from ..utils.sqlite_connection import open_sqlite
from ..utils.time_utils import utc_now_iso
from ..utils.job_consolidation import rebuild_consolidation


class DescriptionStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def connect(self):
        return open_sqlite(self.db_path, row_factory=sqlite3.Row)

    def require_job(self, job_id: str):
        with self.connect() as conn:
            row = conn.execute("SELECT job_id FROM jobs WHERE job_id=? AND LOWER(source)='linkedin'", (job_id,)).fetchone()
        if row is None:
            raise LookupError("Job not found.")

    def enqueue(self, job_ids: list[str], *, refresh=False) -> int:
        added = 0
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for job_id in dict.fromkeys(job_ids):
                # Validate and enqueue the whole selection on one connection.
                # An invalid job rolls back the batch without partial inserts.
                if not conn.execute("SELECT 1 FROM jobs WHERE job_id=? AND LOWER(source)='linkedin'", (job_id,)).fetchone():
                    raise LookupError("Job not found.")
                if not refresh and conn.execute("SELECT 1 FROM saved_job_descriptions WHERE job_id=? LIMIT 1", (job_id,)).fetchone():
                    continue
                url = source_url(job_id)
                added += conn.execute("""INSERT OR IGNORE INTO description_fetches
                    (job_id, source_url, requested_at, refresh, status) VALUES (?, ?, ?, ?, 'queued')""",
                    (job_id, url, utc_now_iso(), int(refresh))).rowcount
        return added

    def unfetched_candidates(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("""SELECT j.job_id FROM jobs j
                WHERE LOWER(j.source)='linkedin' AND (j.archived_at IS NULL OR j.is_favorite=1)
                  AND NOT EXISTS (SELECT 1 FROM description_fetches f WHERE f.job_id=j.job_id)
                  AND NOT EXISTS (SELECT 1 FROM saved_job_descriptions s WHERE s.job_id=j.job_id)
                  AND NOT EXISTS (SELECT 1 FROM job_consolidations c
                      WHERE c.job_id=j.job_id AND c.match_reason='posting_id')
                ORDER BY j.is_favorite DESC, j.last_seen_at DESC, j.job_id""").fetchall()
        candidates = []
        for row in rows:
            try:
                source_url(row[0])
            except ValueError:
                continue
            candidates.append(row[0])
        return candidates

    def recover(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE description_fetches SET status='interrupted', finished_at=?,
                error_kind='interrupted', error_message='Retrieval was interrupted. Retry this job.'
                WHERE status='fetching'""", (utc_now_iso(),))
            self._reuse_saved(conn)
            if conn.execute("SELECT 1 FROM description_fetches WHERE status='queued' LIMIT 1").fetchone():
                conn.execute("UPDATE description_queue_state SET paused=1, reason='Resume the saved queue when ready.' WHERE id=1")

    def failed_candidates(self, limit=10) -> list[str]:
        with self.connect() as conn:
            return [row[0] for row in conn.execute("""SELECT f.job_id FROM description_fetches f
                WHERE f.status IN ('failed','interrupted') AND NOT EXISTS
                  (SELECT 1 FROM description_fetches newer WHERE newer.job_id=f.job_id AND newer.fetch_id>f.fetch_id)
                  AND (f.refresh=1 OR NOT EXISTS (SELECT 1 FROM saved_job_descriptions s WHERE s.job_id=f.job_id))
                ORDER BY f.fetch_id DESC LIMIT ?""", (limit,))]

    @staticmethod
    def _reuse_saved(conn):
        """Complete redundant pending fetches locally; explicit refreshes survive."""
        return conn.execute("""UPDATE description_fetches SET status='unchanged', finished_at=?,
            source_id=(SELECT s.source_id FROM saved_job_descriptions s
                WHERE s.job_id=description_fetches.job_id
                ORDER BY (s.extractor='legacy_job_text'), s.fetched_at DESC, s.source_id DESC LIMIT 1),
            error_message='Using the saved description. No network request was needed.'
            WHERE status='queued' AND refresh=0
                AND EXISTS (SELECT 1 FROM saved_job_descriptions s WHERE s.job_id=description_fetches.job_id)
        """, (utc_now_iso(),)).rowcount

    def pause(self, reason="Paused. The current request may finish.", cooldown_until=None):
        with self.connect() as conn:
            conn.execute("UPDATE description_queue_state SET paused=1, reason=?, cooldown_until=COALESCE(?,cooldown_until) WHERE id=1",
                         (reason, cooldown_until))

    def resume(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT cooldown_until FROM description_queue_state WHERE id=1").fetchone()
            if row[0] and row[0] > utc_now_iso():
                raise ValueError("LinkedIn retrieval is cooling down. Resume after the displayed time.")
            conn.execute("UPDATE description_queue_state SET paused=0, reason=NULL, cooldown_until=NULL WHERE id=1")

    def claim(self) -> dict | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._reuse_saved(conn)
            state = conn.execute("SELECT * FROM description_queue_state WHERE id=1").fetchone()
            if state["paused"] or (state["cooldown_until"] and state["cooldown_until"] > utc_now_iso()):
                return None
            if conn.execute("SELECT 1 FROM description_fetches WHERE status='fetching'").fetchone():
                return None
            row = conn.execute("SELECT * FROM description_fetches WHERE status='queued' ORDER BY fetch_id LIMIT 1").fetchone()
            if row is None:
                return None
            conn.execute("UPDATE description_fetches SET status='fetching', started_at=? WHERE fetch_id=?", (utc_now_iso(), row["fetch_id"]))
            return dict(row)

    def latest_source(self, job_id, *, group=False) -> dict | None:
        with self.connect() as conn:
            selection = """SELECT job_id FROM job_memberships WHERE canonical_job_id =
                (SELECT canonical_job_id FROM job_memberships WHERE job_id=?)""" if group else "SELECT ?"
            row = conn.execute(f"""SELECT s.* FROM description_fetches f
                JOIN description_sources s USING(source_id) WHERE f.job_id IN ({selection})
                    AND f.started_at IS NOT NULL
                    AND s.content_type NOT LIKE 'text/plain%'
                ORDER BY f.fetch_id DESC LIMIT 1""", (job_id,)).fetchone()
        return dict(row) if row else None

    def save_source(self, fetch: dict, response) -> int:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute("SELECT status FROM description_fetches WHERE fetch_id=?", (fetch["fetch_id"],)).fetchone()
            if active is None or active[0] != "fetching":
                raise RuntimeError("This retrieval no longer owns the pending attempt.")
            digest = hashlib.sha256(response.body).hexdigest()
            conn.execute("""INSERT OR IGNORE INTO description_sources
                (job_id, source_url, fetched_at, content_sha256, body, content_type, etag, last_modified)
                VALUES (?,?,?,?,?,?,?,?)""", (fetch["job_id"], fetch["source_url"], utc_now_iso(), digest,
                                            response.body, response.content_type, response.etag, response.last_modified))
            source_id = conn.execute("SELECT source_id FROM description_sources WHERE job_id=? AND content_sha256=?",
                                     (fetch["job_id"], digest)).fetchone()[0]
            conn.execute("UPDATE description_fetches SET source_id=?, http_status=200 WHERE fetch_id=?", (source_id, fetch["fetch_id"]))
            return source_id

    def save_extraction(self, source_id, data, *, extractor=EXTRACTOR, version=EXTRACTOR_VERSION, schema=SCHEMA_VERSION):
        # Each new extractor/schema version appends a result; existing evidence
        # is never rewritten by a parser upgrade or a future enrichment stage.
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            digest = extraction_hash(data)
            existing = conn.execute("""SELECT content_sha256 FROM description_extractions
                WHERE source_id=? AND extractor=? AND extractor_version=? AND schema_version=?""",
                (source_id, extractor, version, schema)).fetchone()
            if existing and existing[0] != digest:
                raise ValueError("Extraction output changed for the same version. Bump the extractor version.")
            conn.execute("""INSERT OR IGNORE INTO description_extractions
                (source_id, extractor, extractor_version, schema_version, extracted_at, content_sha256, data_json)
                VALUES (?,?,?,?,?,?,?)""", (source_id, extractor, version, schema, utc_now_iso(), digest,
                                           json.dumps(data, ensure_ascii=False, sort_keys=True)))
            if extractor in {EXTRACTOR, LEGACY_EXTRACTOR}:
                rebuild_consolidation(conn)

    def finish(self, fetch_id, status, *, source_id=None, http_status=None, error_kind=None, message=None):
        with self.connect() as conn:
            conn.execute("""UPDATE description_fetches SET status=?, finished_at=?,
                source_id=COALESCE(?,source_id), http_status=COALESCE(?,http_status), error_kind=?, error_message=?
                WHERE fetch_id=? AND status='fetching'""",
                (status, utc_now_iso(), source_id, http_status, error_kind, message, fetch_id))

    def job_description(self, job_id) -> dict:
        self.require_job(job_id)
        selection = """SELECT job_id FROM job_memberships WHERE canonical_job_id =
            (SELECT canonical_job_id FROM job_memberships WHERE job_id=?)"""
        with self.connect() as conn:
            attempts = [dict(row) for row in conn.execute(f"""SELECT fetch_id, requested_at, started_at,
                finished_at, status, http_status, error_kind, error_message, source_id
                FROM description_fetches WHERE job_id IN ({selection}) ORDER BY fetch_id DESC LIMIT 8""", (job_id,))]
            row = conn.execute(f"""SELECT s.source_id, s.source_url, s.fetched_at,
                CASE WHEN e.extractor=? THEN 'legacy_text' ELSE 'public_page' END AS source_kind,
                e.extraction_id, e.extractor, e.extractor_version, e.schema_version, e.extracted_at,
                e.content_sha256, e.data_json
                FROM description_sources s JOIN description_extractions e USING(source_id)
                WHERE s.job_id IN ({selection}) AND e.extractor IN (?,?)
                ORDER BY (e.extractor=?), s.fetched_at DESC, s.source_id DESC, e.extraction_id DESC LIMIT 1""",
                (LEGACY_EXTRACTOR, job_id, EXTRACTOR, LEGACY_EXTRACTOR, LEGACY_EXTRACTOR)).fetchone()
            sources = conn.execute(f"SELECT COUNT(*) FROM description_sources WHERE job_id IN ({selection})", (job_id,)).fetchone()[0]
            reparsable = conn.execute(f"SELECT 1 FROM description_sources WHERE job_id IN ({selection}) AND content_type NOT LIKE 'text/plain%' LIMIT 1", (job_id,)).fetchone()
        saved = dict(row) if row else None
        if saved:
            saved["data"] = json.loads(saved.pop("data_json"))
        return {"job_id": job_id, "saved": saved, "attempts": attempts, "source_versions": sources,
                "reparse_available": bool(reparsable), "parser_version": EXTRACTOR_VERSION}

    def queue_status(self) -> dict:
        with self.connect() as conn:
            state = dict(conn.execute("SELECT paused, reason, cooldown_until FROM description_queue_state WHERE id=1").fetchone())
            counts = dict(conn.execute("SELECT status, COUNT(*) FROM description_fetches GROUP BY status").fetchall())
            saved = conn.execute("SELECT COUNT(DISTINCT job_id) FROM saved_job_descriptions").fetchone()[0]
            failed = conn.execute("""SELECT COUNT(*) FROM description_fetches f WHERE status IN ('failed','interrupted')
                AND NOT EXISTS (SELECT 1 FROM description_fetches newer WHERE newer.job_id=f.job_id AND newer.fetch_id>f.fetch_id)
                AND (f.refresh=1 OR NOT EXISTS (SELECT 1 FROM saved_job_descriptions s WHERE s.job_id=f.job_id))""").fetchone()[0]
        return {**state, "paused": bool(state["paused"]), "queued": counts.get("queued", 0),
                "fetching": counts.get("fetching", 0), "saved_jobs": saved, "failed_jobs": failed}
