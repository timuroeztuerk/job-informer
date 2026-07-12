# Job Informer

Job Informer is a personal job-market intelligence tool for collecting postings, tracking repeat sightings, and turning job descriptions into useful signals for one person's search.

It keeps the underlying data visible and reviewable. The goal is not to manage applications or serve a team, but to make it quick to understand which roles are appearing, what employers are asking for, and which opportunities deserve attention.

> **Status:** working prototype under active development.

## What it does

- Collects job postings into a canonical archive and deduplicates repeat results.
- Records each observation, preserving when a role reappears and how long it remains visible.
- Applies conservative filtering to remove student roles and clearly unrelated noise without hiding adjacent technical opportunities.
- Parses descriptions into structured role, skill, and requirement data.
- Scores jobs against a configurable fit profile to support ranking rather than hard exclusion.
- Supports personal review with statuses, priorities, notes, skill gaps, and follow-up dates.
- Exposes the archive, run history, and market summaries through a focused React dashboard.

## Core workflow

**Collect → filter → parse → score → review**

Collection builds the market record. Filtering removes only obvious noise. Parsing makes descriptions comparable, scoring orders the resulting opportunities, and review adds the personal context needed for follow-up. Each stage remains inspectable so an automated decision never becomes a black box.

## Data model

The backend uses a small SQLite database centered on a few durable concepts:

- `jobs` stores one canonical record per posting.
- `scrape_runs` and `job_observations` preserve collection history and repeat sightings.
- `parsed_descriptions` stores versioned structured extraction from job descriptions.
- `job_annotations` holds personal review state and notes.
- `fit_profiles` and `job_fit_scores` persist the ranking profile and its results.
- `filter_decisions`, `llm_attempts`, and parse state tables keep automated processing auditable.

The Python backend owns collection, processing, persistence, and the API. The TypeScript/React frontend provides the dashboard and intelligence views. Generated data stays in backend data and output directories; `frontend/src` remains the single source of truth for frontend code.

## Product principles

- Optimize for one operator's clarity and speed.
- Preserve history instead of overwriting repeat observations.
- Prefer ranking and review over aggressive exclusion.
- Keep automation explainable and data easy to inspect.
- Favor a simple, maintainable prototype over speculative product complexity.
