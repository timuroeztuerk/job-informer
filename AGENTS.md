## Agent Instructions for this project

This project is a PERSONAL, only one user, job market intelligence tool. The core idea is to collect job postings, track repeat sightings, parse descriptions into structured fields, and keep personal annotations for review. The backend is a small SQLite database with tables for jobs, observations, parsed descriptions, annotations, and fit scores. The workflow is: collect, filter, parse, score, review. The product stance is optimized for one operator with a focus on clarity and speed.

Still in progress, do not worry about refactors or cleanup. The main goal is to get a working prototype that can collect job postings, track them, and provide structured insights. Keep the implementation simple and maintainable, and prioritize features that directly support the core workflow of job discovery and tracking.

- Keep `frontend/src` as the single source of truth for frontend code.
- Use `*.ts`/`*.tsx` for frontend implementation files.
- Do not keep compiled/bundled JavaScript outputs in `frontend/src` next to TypeScript sources.
- If you need source duplication during migration, remove the old file family (`.js` when replacing `.ts/.tsx`) in the same commit.
- Prefer one canonical implementation per module or component name; avoid maintaining parallel implementations in different languages.
- For generated artifacts, write only to build/output directories (`frontend/dist`, logs, data files).
- Keep job filters conservative by default: hard-filter only internships/student-study roles and clearly unrelated jobs; avoid broad keyword blacklists that drop adjacent technical roles, and keep company blacklists opt-in rather than default.
- Treat this as a single-user personal tool for job discovery and tracking: prefer simple, maintainable workflows that optimize for one operator's clarity and speed over multi-user abstractions, enterprise-scale architecture, or speculative product complexity.
- Keep project docs minimal and high-signal. For `README.md`, prefer a short product note over a handbook: no setup instructions, no environment-variable inventories, no long endpoint lists, and no generic onboarding material unless explicitly requested. Document only the current purpose, core backend model, and a few key workflow principles.
