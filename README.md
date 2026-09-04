# Job Informer

Job Informer is a fully local, personal tool for collecting LinkedIn jobs, filtering obvious noise, tracking repeat sightings, and reviewing the current market.

> **Status:** Working local prototype under active development. See [ROADMAP.md](ROADMAP.md) for the confirmed development direction and next implementation slice.

## Purpose

The product is designed for one operator with broad role and industry interests. Its job is to build a trustworthy LinkedIn archive, exclude clearly unsuitable roles such as academic and student positions, and make the remaining jobs quick to inspect. It is not an applicant-tracking system or a multi-user service.

## Access and scope

- This is a browser-only desktop UI. Mobile and responsive interfaces, native apps, and other mobile-specific behavior are intentionally out of scope.
- This is a one-person project for one user. Do not introduce accounts, authentication, permissions, teams, collaboration, tenancy, or other multi-user abstractions.

## Core workflow

**Collect → filter → review → inspect**

- **Collect:** manually query multiple pages of LinkedIn public listings using broad role or industry terms and locations, normalize posting identities, and record canonical jobs and repeat sightings.
- **Filter:** keep explicit data-science and analytics/BI targets plus ambiguous adjacent roles, while soft-archiving non-target consulting, engineering, training/recruitment, academic/student, clerical, logistics, purchasing, controlling, HR, support, and other clearly unrelated titles with versioned reasons.
- **Review:** browse new and recurring jobs and archive or restore unsuitable results directly.
- **Inspect:** show a current snapshot of roles, query coverage, companies, and locations without turning the product into a time-series analytics platform.

## Working prototype

- A FastAPI backend and React/TypeScript dashboard run together as a local Docker application.
- One SQLite database is the only application store for canonical jobs, repeat observations, exact query provenance, collection runs, and filter decisions; collection does not create flat-file snapshots.
- LinkedIn pagination uses the public load-more endpoint with a four-page default, records per-query coverage in SQLite, retries rate limits, and has regression coverage for second-page and repeated-page behavior.
- Collection is started manually from one compact UI action and reports essential live progress; detailed run history remains stored in SQLite without occupying the Jobs page.
- The dashboard supports focused search and filters, recency sorting, a minimal job detail pane, archival, and restoration.
- Database readiness checks confirm the local SQLite store before the UI is served.
- A deterministic relevance gate recognizes strong data-science and analytics/BI titles, automatically archives explicit exclusions and clear noise, and leaves ambiguous titles active as `unmatched` for manual inspection.
- The complete LinkedIn archive has been classified under ruleset `2026-09-04.6`; the current filtered dataset is the `filtered_jobs` SQLite view rather than a CSV export.
- Verified SQLite backup/restore and read-only relevance-preview operations protect the archive before any bulk reclassification.
- Intelligence is a small current-state view of collection freshness, volume, companies, locations, target role families, and query groups.
- Indeed, automatic scheduling, fit scoring, email, application stages, follow-ups, and trend views have no active product path. Full job descriptions and optional AI summaries are deferred until collection and filtering quality are established.

## Confirmed direction

- LinkedIn is the only collection source.
- All collection runs are started manually.
- Filtering uses deterministic rules, not AI.
- Academic, study-track, engineering, training, recruitment, non-target consulting, and implementation-heavy analytics consulting jobs are excluded. Plain data-science and data-analytics consulting remains in scope, while other generic non-engineering roles remain discoverable unless a tightly bounded unrelated-role rule matches.
- Collection and filtering quality come before description collection or optional AI summaries; filtering remains deterministic rather than AI-driven.
- Insights describe the current archive rather than trends over time.
- The application remains fully local, with no remote access or application-process tracking.

## Product principles

- Optimize for one operator's clarity and speed.
- Design only for use in a desktop browser.
- Preserve history instead of overwriting repeat sightings.
- Prefer broad discovery and review over aggressive exclusion.
- Keep automated decisions explainable and recoverable.
- Prove multi-page collection and filter behavior with fixtures and tests.
- Favor a simple, maintainable local tool over speculative features.
