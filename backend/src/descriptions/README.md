# Description data contract

This module keeps original evidence separate from network work and interpretation. It uses the same local SQLite archive and one paced worker; it does not fetch on page load or during job collection.

- `description_sources`: immutable response bytes, SHA-256 fingerprint, requested source URL, retrieval time, content type, and conditional-request validators. Identical bytes for the same job reuse one source record. A changed page creates a new record, even if its description text is unchanged.
- `description_fetches`: durable attempts from `queued` to `fetching`, then `succeeded`, `unchanged`, `failed`, or `interrupted`. Each attempt retains timestamps, HTTP status, error classification, and its source reference when bytes were received. Partial unique indexes prevent duplicate pending jobs and concurrent source requests.
- `description_extractions`: immutable JSON results keyed by source, extractor name, extractor version, and schema version. The extraction fingerprint covers normalized output, separately from the raw source fingerprint. Different extractors can share one source without changing each other's results.
- `description_queue_state`: persisted pause reason and rate-limit cooldown. Restart recovery interrupts an in-flight attempt and leaves queued work paused for explicit resumption.

`linkedin.py` owns the public-page client and schema-1 parser. The client derives a fixed LinkedIn URL from the stored numeric posting ID, bounds response size/time, uses conditional requests on explicit refresh, and does not follow redirects. Access restrictions and rate limits pause the queue. Normal reads reuse local data; failed jobs require a deliberate retry.

The parser checks posting identity, reads the description container or an identified `JobPosting` JSON-LD object, preserves paragraph/list boundaries, and records explicitly listed criteria with their labels and evidence selectors. Missing criteria remain absent. The browser renders plain text, never stored HTML.

`store.py` owns short transactions. Network I/O happens outside them. A 200 response is saved before parsing, so an unrecognized layout can be inspected and reparsed later. A failed refresh cannot remove an earlier usable extraction. `service.py` handles pacing, queue processing, recovery, and offline reparsing; API endpoints only orchestrate these operations.

To extend extraction:

1. Define a JSON schema and missing/ambiguous-value behavior. Include source evidence for derived fields; keep the original wording available.
2. Give the extractor a stable name and explicit version. Bump the version whenever output behavior changes, and bump the schema version when its shape or meaning changes.
3. Read a saved source and call `save_extraction` with the source ID, data, extractor name, version, and schema version. Repeating the same version/output is idempotent; changing output under an existing version raises an error.
4. Validate against reviewed fixtures and saved sources before exposing new fields in filters. Never write extraction results into personal flags, favorites, relevance decisions, or sightings.

Legacy `parsed_descriptions` tables are left untouched. The new tables are additive and included in the existing verified SQLite backup workflow.
