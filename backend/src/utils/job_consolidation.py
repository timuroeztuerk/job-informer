"""One review entry per proven role; original postings and evidence stay intact."""

import hashlib
import json
import re
import unicodedata
from collections import defaultdict

from .data_utils import normalize_job_url
from ..descriptions.legacy import LEGACY_EXTRACTOR


def _text(value):
    return " ".join(unicodedata.normalize("NFC", value or "").split())


def _valid_title(title):
    return bool(title) and not re.search(r"archived[\s/-]+placeholder|\bjobs (?:in|near)\b", title, re.I)


def initialize_consolidation(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS job_consolidations (
            job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
            canonical_job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
            match_reason TEXT NOT NULL CHECK(match_reason IN ('posting_id', 'description')),
            CHECK(job_id != canonical_job_id)
        );
        CREATE INDEX IF NOT EXISTS idx_consolidation_canonical ON job_consolidations(canonical_job_id);
        DROP VIEW IF EXISTS job_memberships;
        CREATE VIEW job_memberships AS
            SELECT job_id, canonical_job_id FROM job_consolidations
            UNION ALL
            SELECT j.job_id, j.job_id FROM jobs j
            WHERE NOT EXISTS (SELECT 1 FROM job_consolidations c WHERE c.job_id=j.job_id);
    """)
    # Preserve compatibility with additive legacy columns without duplicating
    # the jobs schema. Only the review fields below are aggregated.
    overrides = {
        "job_id": "g.canonical_job_id",
        "first_seen_at": "g.first_seen_at", "last_seen_at": "g.last_seen_at",
        "scraped_at": "g.scraped_at", "location": "g.locations",
        "is_flagged": "g.is_flagged", "is_favorite": "g.is_favorite",
        "flag_reason": "g.flag_reason", "flagged_at": "g.flagged_at",
        "archived_at": "CASE WHEN g.active_count > 0 THEN NULL ELSE j.archived_at END",
        "archived_reason": "CASE WHEN g.active_count > 0 THEN NULL ELSE j.archived_reason END",
        "seen_count": """CASE WHEN g.posting_count = 1 THEN j.seen_count ELSE (
            SELECT COUNT(DISTINCT COALESCE('run:' || o.scrape_run_id, 'time:' || datetime(o.observed_at)))
            FROM job_observations o JOIN job_memberships m USING(job_id)
            WHERE m.canonical_job_id = g.canonical_job_id) END""",
    }
    columns = ", ".join(f'{overrides.get(row[1], "j." + row[1])} AS "{row[1]}"'
                        for row in conn.execute("PRAGMA table_info(jobs)"))
    conn.execute("DROP VIEW IF EXISTS review_jobs")
    conn.execute(f"""CREATE VIEW review_jobs AS
        WITH groups AS (
            SELECT m.canonical_job_id, COUNT(*) AS posting_count,
                MIN(p.first_seen_at) AS first_seen_at, MAX(p.last_seen_at) AS last_seen_at,
                MIN(p.scraped_at) AS scraped_at,
                GROUP_CONCAT(DISTINCT p.location) AS locations,
                MAX(p.is_flagged) AS is_flagged, MAX(p.is_favorite) AS is_favorite,
                GROUP_CONCAT(DISTINCT CASE WHEN p.is_flagged THEN NULLIF(p.flag_reason, '') END) AS flag_reason,
                MAX(p.flagged_at) AS flagged_at,
                SUM(p.archived_at IS NULL) AS active_count,
                json_group_array(json_object('job_id', p.job_id, 'location', p.location,
                    'url', p.url, 'first_seen_at', p.first_seen_at, 'last_seen_at', p.last_seen_at)) AS postings_json
            FROM job_memberships m JOIN jobs p USING(job_id) GROUP BY m.canonical_job_id
        )
        SELECT {columns}, g.posting_count, g.postings_json
        FROM groups g JOIN jobs j ON j.job_id = (
            SELECT p.job_id FROM job_memberships m JOIN jobs p USING(job_id)
            WHERE m.canonical_job_id = g.canonical_job_id
            ORDER BY (p.title LIKE '%placeholder%'), (p.archived_at IS NULL) DESC,
                p.last_seen_at DESC, p.job_id LIMIT 1)
    """)
    rebuild_consolidation(conn)


def rebuild_consolidation(conn):
    """Recompute from current evidence, so changed descriptions can split a group.

    Posting identity wins over renamed titles/URLs. Different identities require
    the same company, title and full description; location intentionally varies.
    Nothing is deleted from the source tables or their annotation/history tables.
    Caller owns the write transaction.
    """
    cursor = conn.execute("SELECT * FROM jobs ORDER BY job_id")
    columns = [item[0] for item in cursor.description]
    jobs = {}
    for row in cursor:
        job = dict(zip(columns, row))
        jobs[job["job_id"]] = job
    descriptions = {}
    modern = set()
    for job_id, extractor, data in conn.execute("""SELECT s.job_id, e.extractor, e.data_json
        FROM description_sources s JOIN description_extractions e USING(source_id)
        WHERE e.extractor IN ('linkedin_public_job', ?)
        ORDER BY (e.extractor='linkedin_public_job'), s.fetched_at, s.source_id, e.extraction_id""", (LEGACY_EXTRACTOR,)):
        descriptions[job_id] = json.loads(data).get("description_text", "")
        if extractor != LEGACY_EXTRACTOR:
            modern.add(job_id)

    identities = defaultdict(list)
    for job_id, row in jobs.items():
        identity = normalize_job_url(row.get("url"), row["source"])
        if not re.fullmatch(r"linkedin:\d+", identity):
            identity = normalize_job_url(job_id, row["source"])
        identities[identity if re.fullmatch(r"linkedin:\d+", identity) else job_id].append(job_id)

    roles = defaultdict(list)
    for identity, members in identities.items():
        latest = max(members, key=lambda key: (_valid_title(jobs[key]["title"]),
            jobs[key]["last_seen_at"] or "", key))
        row = jobs[latest]
        description = _text(descriptions.get(latest))
        # Short snippets / empty descriptions are not enough evidence.
        if (len(description) >= 200 and _valid_title(row["title"])
                and _text(row["company"]).casefold() not in {"", "unknown", "not specified", "n/a"}):
            fingerprint = hashlib.sha256(description.encode()).hexdigest()
            key = (row["source"].casefold(), _text(row["company"]).casefold(),
                   _text(row["title"]).casefold(), fingerprint)
        else:
            key = (identity,)
        roles[key].append((identity, members))

    existing = dict(conn.execute("SELECT job_id, canonical_job_id FROM job_consolidations"))
    roots = set(existing.values())
    links = []
    for identity_groups in roles.values():
        members = [job_id for _, group in identity_groups for job_id in group]
        if len(members) < 2:
            continue
        canonical = min(members, key=lambda key: (
            not _valid_title(jobs[key]["title"]), key not in roots,
            key not in modern, not bool(re.fullmatch(r"linkedin:\d+", key)),
            jobs[key]["first_seen_at"] or "", key))
        canonical_identity = next(identity for identity, group in identity_groups if canonical in group)
        for identity, group in identity_groups:
            links.extend((key, canonical, "posting_id" if identity == canonical_identity else "description")
                         for key in group if key != canonical)
    conn.execute("DELETE FROM job_consolidations")
    conn.executemany("INSERT INTO job_consolidations VALUES (?, ?, ?)", links)
    return {"groups": len({link[1] for link in links}), "consolidated_postings": len(links)}


def canonical_job_id(conn, job_id):
    row = conn.execute("SELECT canonical_job_id FROM job_memberships WHERE job_id=?", (job_id,)).fetchone()
    return row[0] if row else job_id


def member_job_ids(conn, job_id):
    return [row[0] for row in conn.execute(
        "SELECT job_id FROM job_memberships WHERE canonical_job_id=?", (canonical_job_id(conn, job_id),))]
