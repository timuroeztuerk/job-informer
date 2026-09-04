# Job Informer

Job Informer is a fully local, personal tool for collecting LinkedIn jobs, filtering obvious noise, tracking repeat sightings, and reviewing the current market.

> **Status:** Working local prototype under active development. See [ROADMAP.md](ROADMAP.md) for the confirmed development direction and next implementation slice.

## Purpose

The product is designed for one operator with broad role and industry interests. Its job is to build a trustworthy LinkedIn archive, exclude clearly unsuitable roles such as academic and student positions, and make the remaining jobs quick to inspect. It is not an applicant-tracking system or a multi-user service.

## Core workflow

**Collect → filter → review → inspect**

- **Collect:** manually query multiple pages of LinkedIn public listings using broad role or industry terms and locations, normalize posting identities, and record canonical jobs and repeat sightings.
- **Filter:** apply deterministic, explainable rules for academic, student, and clearly unrelated roles. Filtering should be conservative and every decision recoverable.
- **Review:** browse new and recurring jobs, archive unsuitable results, and keep lightweight personal notes where useful.
- **Inspect:** show a current snapshot of roles, query coverage, companies, and locations without turning the product into a time-series analytics platform.

## Working prototype

- A FastAPI backend and React/TypeScript dashboard run together as a local Docker application.
- One SQLite database is the only application store for canonical jobs, repeat observations, collection runs, annotations, and filter decisions; collection does not create flat-file snapshots.
- LinkedIn pagination uses a four-page default, records per-query coverage in SQLite, retries rate limits, and has regression coverage for second-page and repeated-page behavior.
- Collection and maintenance jobs can be started from the UI, report structured progress, and retain recent run history.
- The dashboard supports search, date/company/review filters, sorting by recency or recurrence, job detail review, archival, and restoration.
- Database readiness and integrity checks protect the local data store.
- The codebase still contains experimental Indeed, scheduling, LLM parsing and cleanup, fit scoring, email, and trend features. These are outside the current direction and will be removed from the active workflow or deferred.

## Confirmed direction

- LinkedIn is the only collection source.
- All collection runs are started manually.
- Filtering uses deterministic rules, not AI.
- Academic and study-track jobs are excluded; broad commercial roles and industries remain discoverable.
- Collection and filtering quality come before fit scoring.
- Insights describe the current archive rather than trends over time.
- The application remains fully local, with no remote access or application-process tracking.

## Product principles

- Optimize for one operator's clarity and speed.
- Preserve history instead of overwriting repeat sightings.
- Prefer broad discovery and review over aggressive exclusion.
- Keep automated decisions explainable and recoverable.
- Prove multi-page collection and filter behavior with fixtures and tests.
- Favor a simple, maintainable local tool over speculative features.
