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
    refresh INTEGER NOT NULL DEFAULT 0 CHECK(refresh IN (0,1)),
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


def initialize_schema(conn):
    conn.executescript(SCHEMA)
    if "refresh" not in {row[1] for row in conn.execute("PRAGMA table_info(description_fetches)")}:
        conn.execute("ALTER TABLE description_fetches ADD COLUMN refresh INTEGER NOT NULL DEFAULT 0 CHECK(refresh IN (0,1))")
        # The old enqueue code only queued a saved public description when an
        # explicit refresh was requested. Preserve those pending refreshes.
        conn.execute("""UPDATE description_fetches SET refresh=1
            WHERE status IN ('queued','fetching') AND EXISTS (
                SELECT 1 FROM description_sources s JOIN description_extractions e USING(source_id)
                WHERE s.job_id=description_fetches.job_id AND e.extractor='linkedin_public_job'
                  AND e.extracted_at <= description_fetches.requested_at)""")
    conn.execute("""CREATE VIEW IF NOT EXISTS saved_job_descriptions AS
        SELECT member.job_id, s.source_id, s.fetched_at, e.extractor
        FROM description_sources s JOIN description_extractions e USING(source_id)
        JOIN job_memberships owner ON owner.job_id=s.job_id
        JOIN job_memberships member ON member.canonical_job_id=owner.canonical_job_id
        WHERE e.extractor IN ('linkedin_public_job', 'legacy_job_text')""")
