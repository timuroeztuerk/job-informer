# Job Informer Roadmap

> **Last reviewed:** 2026-09-04  
> **Planning status:** The core collector works. The next phase is to improve meaningful job discovery through broader Germany/Switzerland coverage and evidence-based search and filter refinement.

This roadmap tracks only the current boundary and ordered future work. Completed implementation history belongs in Git and tests rather than a permanent checklist.

## Product boundary

- One person uses the application locally in a desktop browser.
- LinkedIn is the only collection source, and every collection is started manually.
- The intended search scope starts with Data Scientist and Data Analyst roles across Germany and Switzerland.
- SQLite is the canonical store for jobs, sightings, query provenance, filter decisions, and run history.
- Filtering is deterministic, explainable, conservative, and recoverable.
- The active workflow is collect, filter, review, and inspect through one canonical job list.
- Mobile UI, hosted access, multi-user behavior, automatic scheduling, fit scoring, application tracking, personal notes, keyboard review controls, and bulk review actions are not planned.
- Full job descriptions and AI-generated summaries are explicitly deferred until collection and filtering quality are established. AI must not decide which jobs are collected or filtered.

## Current baseline

- Multi-page LinkedIn collection records page coverage, stop reasons, failures, rate limits, canonical jobs, and repeat sightings.
- Every valid sighting is stored before an automatic archive decision is applied.
- Relevance rules exclude explicit non-target roles while retaining ambiguous adjacent titles for review.
- The complete LinkedIn archive is classified under ruleset `2026-09-04.6`, and automatic decisions remain auditable and reversible.
- The dashboard provides collection status, search and focused filters, direct archive/restore review, recurrence context, a minimal detail pane, and a compact current-state snapshot.
- Database readiness checks and verified SQLite backup/restore commands protect the local archive.

## Now — improve meaningful discovery

1. Add country-wide Germany and Switzerland collection while keeping Data Scientist and Data Analyst as the initial role queries.
2. Compare country-wide results with the current Berlin, Stuttgart, Frankfurt, and München queries before removing city-level searches; measure unique jobs, duplicate coverage, page completion, and request failures.
3. Run the collector manually on multiple days and verify that broader coverage remains reliable.
4. Review active unmatched jobs for irrelevant results and newly automatic-archived jobs for meaningful jobs that were filtered incorrectly.
5. Refine search terms and deterministic rules only when repeated results show a specific coverage or precision problem.

This phase is complete when Germany and Switzerland are covered reliably, important jobs are not systematically filtered out, and the active list has a manageable proportion of irrelevant results.

## Next — add a favorite flag

1. Add one boolean favorite field with a backward-compatible default.
2. Allow favoriting from the canonical job list or detail pane and filtering the list to favorites.
3. Keep favorites independent from archive/restore state.
4. Do not reintroduce personal notes, review statuses, priorities, keyboard workflows, or bulk actions.

## Much later — descriptions and AI summaries

Start this phase only after collection and filtering are reliable across Germany and Switzerland.

1. Collect and store full job descriptions with source provenance and fetch timestamps.
2. Make description retrieval resumable and avoid unnecessary refetches.
3. Expose the original description for inspection before adding derived data.
4. Define and evaluate a concise AI-summary format on reviewed descriptions.
5. Keep AI summaries optional and separate from deterministic collection and filtering decisions.

## Engineering guardrails

- Keep one canonical TypeScript implementation for each frontend module and generated files outside `frontend/src`.
- Keep schema changes backward-compatible with the personal database and create a verified backup before destructive repair.
- Expand tests when collection, identity, observations, filtering, run recovery, or archive persistence changes.
- Keep LinkedIn-specific parsing isolated from the core data model and treat network collection as fallible.
- Prefer deleting unused product paths over maintaining speculative abstractions.
