# Job Informer Roadmap

> **Last reviewed:** 2026-09-04  
> **Planning status:** Direction confirmed; collector stabilization is in progress.

This roadmap tracks outcomes rather than release dates. Keep only a small number of items in **Now**, move completed work into the baseline, and revisit the order whenever the search strategy changes.

## Product objective

Build a trustworthy, manual LinkedIn collector that captures multiple result pages, applies conservative deterministic filters, and presents a useful snapshot of the remaining jobs.

The product is fully local and single-user. It supports broad job discovery and present-day market inspection, not application management or historical market analysis.

## Confirmed scope

### In scope

- LinkedIn as the only job source.
- Manual collection runs only.
- Multiple result pages for every configured keyword and location query.
- Broad role and industry-oriented search terms with deterministic, explainable filtering.
- Hard exclusion of academic and study-track jobs.
- Canonical jobs, repeat sightings, lightweight review, and current-state summaries.
- SQLite as the canonical application store.
- A local FastAPI, React/TypeScript, and SQLite application.

### Deferred or out of scope

- Indeed or other collection sources.
- Scheduled or otherwise automatic collection runs.
- AI-based filtering and other LLM-dependent workflow steps.
- Fit scoring until collection and filtering meet their completion criteria.
- Application-stage tracking, follow-up automation, and document workflows.
- Trend analysis and other over-time intelligence.
- Remote access, hosted deployment, and multi-user behavior.

## Working now

- [x] Collect public LinkedIn listings by keyword, location, and time window.
- [x] Normalize provider URLs and identities to deduplicate postings.
- [x] Store canonical jobs, collection runs, and repeat observations in SQLite.
- [x] Archive and restore stored jobs while retaining filter audit records.
- [x] Review jobs with statuses, priorities, notes, skill gaps, follow-up dates, and resume versions.
- [x] Surface review queues, recurrence, freshness, market summaries, run history, and maintenance actions in the local dashboard.
- [x] Support manual collection with structured progress.
- [x] Check database readiness and repair legacy orphaned records with a pre-repair backup.
- [x] Request up to four LinkedIn result pages by default using 25-result offsets.
- [x] Persist per-query page coverage, stop reasons, request failures, and rate limits in SQLite.
- [x] Retry the same LinkedIn request after a 429 cooldown instead of skipping later searches.
- [x] Store valid sightings before applying deterministic archive rules.
- [x] Filter explicit English and German academic-role titles.
- [x] Stop automatic fit-score recomputation during collection.

The code also contains working or experimental features that are no longer in the chosen scope. Removing them from the active workflow is part of the next milestone.

## Now — narrow and stabilize the collector

### 1. Remove inactive product paths

- [ ] Remove Indeed from runtime configuration, collection orchestration, maintenance controls, and user-facing copy.
- [ ] Remove interval scheduling so every collection starts with an explicit operator action.
- [ ] Remove AI purge from collection, cleanup, and dashboard actions.
- [ ] Remove LLM parsing and fit scores from the remaining active UI while preserving existing databases without a destructive migration.
- [ ] Remove email and follow-up-oriented controls that do not serve the simplified review flow.

### 2. Prove multi-page LinkedIn collection

Pagination now loops over four pages by default, has focused regression tests, and reports aggregate coverage in run results. The remaining work is to make search groups and ambiguous-role fixtures equally explicit.

- [x] Add fixtures for at least two distinct LinkedIn result pages and verify offsets `0`, `25`, and later pages are requested in order.
- [x] Verify that results from page two and beyond are collected, deduplicated, and counted in the same query report.
- [ ] Represent broad role and industry searches as simple query groups and retain the matching group on each observation.
- [x] Make stop conditions explicit: empty page, repeated page, configured page limit, total-job limit, or request failure.
- [x] Detect a repeated response page so LinkedIn cannot create a wasteful pagination loop.
- [x] Record pages attempted, pages completed, raw cards, valid jobs, duplicates, and failures for each keyword/location query.
- [x] Show total page coverage and request failures in the run summary.

### 3. Make filtering deterministic and conservative

- [x] Replace the broad default keyword blacklist with a small personal exclusion list for AI Engineer titles.
- [x] Hard-filter student and study-track roles using explicit patterns.
- [x] Add explicit academic-role rules for positions such as professor, lecturer, postdoctoral, doctoral, and university research-assistant roles without rejecting commercial research or research-scientist jobs by keyword alone.
- [ ] Define a small set of clearly unrelated role families only after reviewing real false positives.
- [ ] Do not use a default company blacklist.
- [x] Persist each valid sighting before applying an archive decision so rejected jobs remain auditable and recoverable.
- [x] Store the matched rule, human-readable reason, and scrape-run identifier for every automatic archive.
- [ ] Add keep/reject fixtures for ambiguous adjacent roles, especially engineering, software, research, analytics, and project positions.
- [x] Report kept and filtered counts by rule after every run.

### 4. Consolidate storage

SQLite is the sole application store. The legacy per-run CSV snapshots and CSV import/export paths have been removed.

- [x] Stop writing a CSV snapshot after every successful collection.
- [x] Remove legacy CSV snapshots and sample data from the repository data directory.
- [x] Keep jobs, observations, filter decisions, and collection coverage in SQLite only.
- [x] Remove CSV migration and export helpers so new flat-file stores cannot be created accidentally.
- [ ] Add an explicit SQLite backup command and a tested restore command for routine recovery.
- [x] Do not add flat-file exports unless the product direction explicitly changes.

### Milestone: trustworthy collector

A manual run demonstrably collects more than one LinkedIn page, never silently drops a valid sighting, explains every filtered job, and clearly reports partial collection failures.

## Next — simplify review and current-state insights

- [ ] Make newly collected and recurring jobs the primary review queues; do not depend on fit scores.
- [ ] Keep search and filters focused on title, company, industry when available, location, review status, and recurrence.
- [ ] Add keyboard-first review and safe bulk archive/restore actions if they materially shorten review.
- [ ] Show compact snapshot counts for broad titles, query groups, companies, and locations.
- [ ] Remove trend, parser-health, skill-gap, and fit-coverage panels from the main intelligence view.
- [ ] Keep collection freshness, run health, database identity, and integrity visible as operational signals.

### Milestone: daily review loop

The operator can start a collection, confirm its coverage, and review the useful new or recurring jobs in one short session.

## Later — reconsider fit scoring

- [ ] Define what a score would improve beyond title, industry, and deterministic filters.
- [ ] Establish a reviewed set of kept and rejected jobs as an evaluation fixture.
- [ ] Implement a transparent non-AI baseline before considering description parsing or LLM signals.
- [ ] Add fit scoring only if it measurably improves review ordering without narrowing broad discovery.

### Entry criteria

Multi-page collection is reliable, filtering false positives are acceptably low, filter decisions are auditable, and the manual review queue is still too noisy to scan efficiently.

## Ongoing engineering guardrails

- [ ] Keep one canonical TypeScript implementation for each frontend module and generated files outside `frontend/src`.
- [ ] Keep schema migrations backward-compatible with the existing personal database and back up before destructive repair.
- [ ] Expand tests around LinkedIn pagination, identity, observations, filtering, run recovery, and review-state persistence whenever those paths change.
- [ ] Bind operational access to the local machine and avoid optional external-service dependencies beyond LinkedIn access.
- [ ] Treat LinkedIn scraping as fallible and keep source-specific parsing isolated from the core data model.
- [ ] Keep routine database backups recoverable and schema changes compatible with the existing personal database.
- [ ] Prefer deleting unused product paths over maintaining speculative abstractions.

## Next implementation slice

1. Remove automatic scheduling and AI cleanup from the collection path.
2. Remove Indeed from the configured and user-facing source set.
3. Add explicit SQLite backup and restore commands for routine recovery.
4. Expand keep/reject fixtures around ambiguous adjacent roles before adding any new hard filter.
5. Remove remaining fit-score and LLM presentation from the active dashboard.
