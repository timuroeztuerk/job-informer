# Job Informer

Job Informer is a single-user job market intelligence tool.

Its job is simple: collect roles, keep them inspectable, and make it easier to see what the market is asking for over the next few months. It is not an applicant tracking system, not a team product, and not meant to hide the data behind heavy workflow.

## Core idea

Each job is stored once as the canonical record. Repeat sightings are tracked separately, so the project can answer not just "what exists" but also "what keeps showing up" and "what stays open." Structured parsing turns raw descriptions into usable fields, personal annotations keep review lightweight, and profile-fit scoring ranks jobs against the current target without narrowing the search too early.

## Backend model

The backend revolves around a small SQLite system in `backend/data/jobs.db`.

`jobs` holds the canonical posting. `job_observations` and `scrape_runs` capture repeat sightings and run history. `parsed_descriptions` stores structured AI extraction. `job_annotations` stores personal notes, status, priority, and follow-up context. `fit_profiles` and `job_fit_scores` keep ranking persistent instead of recomputing ad hoc in the UI.

## Workflow

The product flow is: collect, filter, parse, score, review.

Filtering is intentionally conservative. The default goal is to remove internships, thesis roles, working-student roles, and clearly irrelevant noise without throwing away adjacent technical roles. Scoring is broad by design: it helps rank the market, not prematurely collapse it.

## Product stance

This project is optimized for one operator. The backend and frontend should stay explicit, inspectable, and easy to change. The most important outputs are:

- a clean job archive
- repeat-observation history
- structured skill and role signals
- lightweight personal notes
- a simple ranking layer that helps decide what to study next
