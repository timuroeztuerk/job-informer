# Job Informer Roadmap

> **Last reviewed:** 2026-09-05
>
> **Planning status:** Original descriptions and reviewed personal filters now run alongside collection-quality validation. The first 31 flagged examples inform ruleset `2026-09-05.2`; six unexplained examples still need more evidence. Two of three comparable collection days are recorded; further structured extraction follows review of saved descriptions. AI remains deferred.

This roadmap tracks only the current boundary and ordered future work. Completed implementation history belongs in Git and tests rather than a permanent checklist.

## Product boundary

- One person uses the application locally in a desktop browser.
- LinkedIn is the only collection source, and every collection is started manually.
- The intended search scope starts with Data Scientist and Data Analyst roles across Germany and Switzerland.
- SQLite is the canonical store for jobs, sightings, query provenance, filter decisions, run history, original description sources, and versioned extractions.
- Filtering is deterministic, explainable, conservative, and recoverable.
- The active workflow is collect, filter, read original descriptions, review, and inspect through one canonical job list.
- Mobile UI, hosted access, multi-user behavior, automatic scheduling, fit scoring, application tracking, general personal notes, keyboard review controls, and bulk review actions are not planned.
- Original descriptions are collected manually while coverage and filter audits continue. AI-generated summaries remain deferred; AI must not decide which jobs are collected or filtered.

## Current baseline

- Multi-page LinkedIn collection records page coverage, stop reasons, failures, rate limits, canonical jobs, and repeat sightings.
- Country-wide Germany and Switzerland searches run beside retained Berlin, Stuttgart, Frankfurt, and München searches, with per-run yield, overlap, page completion, and failure comparison.
- Intelligence counts distinct healthy collection days for matching requested terms, locations, and search windows. It shows each retained city's added results, the underlying attempts and coverage concerns, and links to unmatched, automatic-archive, and flagged-example audits. Same-day repeats and incomplete runs cannot inflate the day count; collection checks do not certify filter quality.
- Every valid sighting is stored before an automatic archive decision is applied.
- Relevance rules exclude explicit non-target roles while retaining ambiguous adjacent titles for review.
- Relevance ruleset `2026-09-05.2` applies reviewed personal title preferences before target/adjacent matching: SAP, CRM, pricing/risk, accounting, mathematician/bioinformatics roles, director/principal/executive roles, Fachspezialist/Kaufmann, demand planning, and hot-forming scientists. Study and academic variants include Masterand, PreMaster, degree-course adverts, Professur, and Scientific Employee. Senior/lead roles and adjacent engineering remain reviewable; manual restores take precedence. Stored decisions include matched rules and their version.
- The dashboard provides collection status, focused filters, favorites, archive/restore review, recurrence context, and a minimal detail pane. Intelligence combines time-windowed company/city rankings, role mix, recurring jobs, filter audits, and collection coverage with matching job-list drill-downs.
- A URL-persisted relevance filter supports unmatched sampling and an automatic-archive audit based on recorded rule decisions, with manual decisions kept distinct.
- Jobs can be flagged as mismatch examples, optionally with a reason, and retrieved with the Flagged only filter. Flags survive repeat sightings and archive/restore actions; rule changes still require reviewing the examples.
- Jobs expose saved original descriptions and explicitly listed seniority, employment type, job function, and industry criteria. Retrieval supports one job or batches of ten, with favorites prioritized, deduplication, conditional refresh, pause/resume, explicit retries, and restart recovery without automatic network work.
- Immutable source bytes and fingerprints are separate from versioned parsing results. Failed refreshes retain the last usable description; offline reparsing and future extractors can reuse the same source evidence. Flags, favorites, relevance, and sightings remain independent.
- Database readiness checks and verified SQLite backup/restore commands protect the local archive.
- One dependency-cached `make test` command runs isolated backend and frontend suites concurrently without network access. Temporary SQLite files use RAM storage; targeted runs accept `BACKEND_TESTS` (Python module) or `FRONTEND_TESTS` (file name).

## Now — prove collection and filter quality

1. Complete the next matching one-day-window collection on September 6 or later, using the same two search terms, both countries, and all four retained cities. Inspect Intelligence's collection evidence after the run; at least three separate days are required.
2. Treat partial pagination, request failures, or unexplained zero-yield queries as collection problems rather than evidence about search coverage.
3. After each run, sample active `unmatched` jobs for obvious noise and automatically archived jobs for meaningful false exclusions. Review flagged examples and their reasons alongside those samples; turn every confirmed mistake into a regression fixture before changing a rule.
4. Keep the retained city searches unless country-wide searches repeatedly cover most city results without losing meaningful city-only roles.
5. Change search terms or deterministic rules only in response to a repeated, documented coverage or precision problem.

This phase is complete when three separate collection days finish without material coverage failures, sampled filter decisions reveal no systematic false exclusions, and there is enough evidence to keep or remove each retained city search deliberately.

Current evidence: matching one-day-window collections on September 4 and 5 completed 38/38 and 37/37 pages without request failures or zero-yield queries. Country searches covered 19.3% and 20.1% of city results; retain all four city searches. A third separate collection day is still needed. The additional September 5 seven-day-window run is stored separately and does not count as another validation day.

Each retained city added results on both validated days:

| City search | Days adding results | Results absent from country searches | Covered by country searches |
| --- | ---: | ---: | ---: |
| Berlin | 2 / 2 | 62 | 32.6% |
| Stuttgart | 2 / 2 | 65 | 16.7% |
| Frankfurt | 2 / 2 | 80 | 15.8% |
| München | 2 / 2 | 27 | 3.6% |

These totals include archived jobs and count returning jobs once per daily sample; the same posting can appear in multiple cities. They demonstrate added collection coverage, while manual review must establish the relevance of the city-only roles.

The September 5 audit sampled 20 unmatched and 20 automatically excluded/unrelated archive records across the archive, plus 12 from each group in each new run. Repeated exclusion of `Machine Learning Ops Engineer` is covered by a regression fixture and corrected to adjacent/unmatched; five affected jobs were restored after a verified backup. Review `Power BI Developer` (`linkedin:4460702102`) and `Machine Learning Consultant` (`linkedin:4462150754`) against the intended scope before further rule changes. One airport-security title (`linkedin:4461126643`) remains a documented unmatched-noise case to check for recurrence.

The September 5 personal-filter review covers all 31 flagged examples in [a regression fixture](backend/tests/fixtures/flag_relevance_cases.json). Clear notes and repeated narrow families account for 25 examples. The reviewed preview identifies 120 active jobs for automatic archiving (2,041 → 1,921 active), with no favorites or manual keeps affected. The rules are deployed; applying this batch to the existing archive awaits explicit approval. Guard cases retain senior roles, Master Data, contextual executive-office titles, general finance/HR analytics, fraud analytics, and demand forecasting. Six single unexplained flags remain outside the new rules: FinOps analyst, fraud manager, AI audit specialist, bank/regulatory-reporting data engineer, MSAT data expert, and the leading Zahlen-/Datenanalyse role. Review their reasons before generalizing further; a new flag alone never changes the rules.

## Now — validate original descriptions

This work proceeds alongside the collection-quality checks above.

1. Review a small manually fetched sample across companies, languages, and posting ages. Check paragraph/list fidelity and the accuracy of explicitly listed criteria.
2. Keep unavailable, blocked, rate-limited, and unparseable responses distinct. Retain earlier usable text, inspect failures, and resume only through a manual action.
3. Add a regression fixture for each confirmed layout or extraction mistake. Parser changes must bump the extractor version; reparse saved sources to compare results without refetching.

Initial live check on September 5: three postings across Fraunhofer ITMP, Deloitte, and Thomson Reuters were fetched successfully, preserving 21,102 description characters and twelve listed criteria. The sample covers German and English text. Offline reparsing and page reload kept the first posting at one source, one extraction, and one fetch attempt. The queue finished empty; the existing archive and personal annotations were preserved.

## Next — extract additional structured information

1. Choose fields from actual review needs, such as required skills, experience, education, language, work arrangement, or compensation.
2. Define each field's schema, missing-value behavior, conflict handling, and source evidence before extracting broadly. Do not turn absent information into a negative claim or infer remote work from a location label. Listed criteria can disagree with the body: the initial Fraunhofer sample lists full-time while describing a 19.5-hour week.
3. Add a separate versioned extractor over a saved source, keeping its results independent from original text and personal annotations. New parser or schema versions append results rather than overwriting prior evidence.
4. Evaluate on a small reviewed set before adding filters or Intelligence metrics. Extraction must not silently archive or reclassify jobs.

The implementation contract is in [backend/src/descriptions/README.md](backend/src/descriptions/README.md).

## Later — optional AI summaries

Start this phase only after original description retrieval is reliable and enough descriptions have been reviewed manually.

1. Define a concise summary schema from actual review needs.
2. Evaluate it on a small reviewed set before storing generated summaries broadly.
3. Store model and prompt-version provenance with every derived summary.
4. Keep summaries optional and separate from deterministic collection and filtering decisions.

## Engineering guardrails

- Keep one canonical TypeScript implementation for each frontend module and generated files outside `frontend/src`.
- Keep schema changes backward-compatible with the personal database and create a verified backup before destructive repair.
- Use `make test` for the isolated backend and frontend suites, and expand regression fixtures when collection, identity, observations, filtering, run recovery, or archive persistence changes.
- Keep LinkedIn-specific parsing isolated from the core data model and treat network collection as fallible.
- Prefer deleting unused product paths over maintaining speculative abstractions.
