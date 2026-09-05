"""Import previously collected text without inventing an HTML response or fetch."""

import hashlib
import json

from .linkedin import extraction_hash
from ..utils.time_utils import utc_now_iso


LEGACY_EXTRACTOR = "legacy_job_text"


def import_legacy_descriptions(conn):
    if "description" not in {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}:
        return 0
    imported = 0
    for job_id, text, title, url, collected_at in conn.execute("""
        SELECT job_id, description, title, COALESCE(url, ''),
            COALESCE(scraped_at, first_seen_at, created_at)
        FROM jobs WHERE description IS NOT NULL AND TRIM(description) != ''
    """).fetchall():
        if not text.strip() or text.strip() == "FETCH_FAILED":
            continue
        body = text.encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        imported += conn.execute("""INSERT OR IGNORE INTO description_sources
            (job_id, source_url, fetched_at, content_sha256, body, content_type)
            VALUES (?, ?, ?, ?, ?, 'text/plain; charset=utf-8')""",
            (job_id, url, collected_at, digest, body)).rowcount
        source_id = conn.execute("SELECT source_id FROM description_sources WHERE job_id=? AND content_sha256=?",
                                 (job_id, digest)).fetchone()[0]
        data = {"description_text": text, "title": title, "criteria": [],
                "evidence": {"source_url": url, "description_locator": "jobs.description"}}
        conn.execute("""INSERT OR IGNORE INTO description_extractions
            (source_id, extractor, extractor_version, schema_version, extracted_at, content_sha256, data_json)
            VALUES (?, ?, '1.0.0', '1', ?, ?, ?)""",
            (source_id, LEGACY_EXTRACTOR, utc_now_iso(), extraction_hash(data),
             json.dumps(data, ensure_ascii=False, sort_keys=True)))
    return imported
