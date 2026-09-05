"""Additive schema, independent of legacy description experiments."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS description_sources (
    source_id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    body BLOB NOT NULL,
    content_type TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    UNIQUE(job_id, content_sha256)
);
CREATE TABLE IF NOT EXISTS description_extractions (
    extraction_id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES description_sources(source_id) ON DELETE RESTRICT,
    extractor TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    data_json TEXT NOT NULL CHECK(json_valid(data_json)),
    UNIQUE(source_id, extractor, extractor_version, schema_version)
);
CREATE TABLE IF NOT EXISTS description_fetches (
    fetch_id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
    source_url TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('queued','fetching','succeeded','unchanged','failed','interrupted')),
    http_status INTEGER,
    error_kind TEXT,
    error_message TEXT,
    source_id INTEGER REFERENCES description_sources(source_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX IF NOT EXISTS description_one_pending_per_job
    ON description_fetches(job_id) WHERE status IN ('queued','fetching');
CREATE UNIQUE INDEX IF NOT EXISTS description_one_request_at_a_time
    ON description_fetches((1)) WHERE status = 'fetching';
CREATE INDEX IF NOT EXISTS description_fetch_history ON description_fetches(job_id, fetch_id DESC);
CREATE INDEX IF NOT EXISTS description_source_history ON description_sources(job_id, source_id DESC);
CREATE TABLE IF NOT EXISTS description_queue_state (
    id INTEGER PRIMARY KEY CHECK(id=1),
    paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
    reason TEXT,
    cooldown_until TEXT
);
INSERT OR IGNORE INTO description_queue_state(id) VALUES(1);
"""
