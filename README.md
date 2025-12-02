# Job Informer

Job Informer scrapes fresh data-focused job postings, filters them with lightweight LLM heuristics, and delivers concise email digests.

## Quick Start
- Ensure Python 3.11+ is installed.
- Install backend deps: `pip install -r backend/requirements.txt`
- Copy `backend/.env.example` → `backend/.env` and fill in credentials, especially `OPENAI_API_KEY` and email SMTP settings.
- Run a one-off scrape: `python backend/main.py --run-once`

## Minimal API for the frontend
- Start the API from repo root: `uvicorn backend.api:app --reload --port 8000`
- (Optional) set `API_TOKEN` in your environment to require `x-api-token` on all requests.
- Trigger a scrape: `curl -X POST http://localhost:8000/runs` (add `-H "x-api-token: <token>"` if set)
- Poll run status: `curl http://localhost:8000/runs/<run_id>`
- List recent runs: `curl http://localhost:8000/runs`
- Read jobs: `curl 'http://localhost:8000/jobs?limit=20'`
- Filter jobs: add `search`, `source`, `company`, `date_from`, `date_to`, `sort=scraped_at_desc|title_asc`
- Single job: `curl http://localhost:8000/jobs/<job_id>`

## Frontend (Vue)
- `cd frontend && npm install`
- Copy `.env.example` → `.env` and set `VITE_API_BASE` (defaults to `http://localhost:8000`). If you set `API_TOKEN` on the backend, also set `VITE_API_TOKEN`.
- Run dev server: `npm run dev` (Vite on port 5173).
- The UI can start runs, watch status, list jobs, and open details/descriptions.

## Docker (one command for frontend + backend)
- Put your secrets in a single `.env` at repo root (see `.env.example`). Set `API_TOKEN` once, reuse it for both backend and frontend with `VITE_API_TOKEN=...`. Set `VITE_API_BASE=http://localhost:8000` for local, or change if hosting elsewhere.
- Build and run both services: `docker compose up --build`
- Backend: http://localhost:8000 (volumes mount `./backend/data` and `./backend/logs`).
- Frontend: http://localhost:5173 (static build served via nginx).

## Common Tasks
- `python backend/main.py --run-once --keywords "Data Scientist" --locations "Berlin"` – scrape with custom filters.
- `python backend/main.py --parse-descriptions` – fetch any missing descriptions and parse structured summaries in one go.
- `python backend/main.py --purge` – apply rules-based cleanup and AI-powered purging in a single run.

## Configuration Highlights
- `OPENAI_MODEL` (default `gpt-5-mini`) drives all LLM calls; override `DESC_PARSER_MODEL` for the parser only.
- Email delivery requires `SMTP_SERVER`, `SMTP_PORT`, `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, and `RECIPIENT_EMAIL`.
- Tune scraping scope with `SEARCH_KEYWORDS`, `SEARCH_LOCATIONS`, and `SEARCH_TIME_RANGE` (`day|week|month`).

## Data & Logs
- SQLite databases live in `backend/data/`; historical scrapes are retained for de-duplication and auditing.
- AI parsing outputs are cached in `parsed_descriptions` to avoid redundant LLM calls.
- Runtime logs stream to `backend/logs/job_informer.log` (level configured via `LOG_LEVEL`).

- ## Contributor Notes
- `--parse-descriptions` now fetches missing descriptions before parsing; the deprecated `--get-descriptions` flag was removed from the UI. Keep frontend mode shortcuts aligned with backend CLI modes when adding new ones.
- `--purge` now runs both rules-based cleanup and the AI purge; `--ai-purge` remains only as a backend alias. The frontend surfaces `--purge` as the single purge action, and the test button is intentionally absent there (use the terminal instead).
- The summary view and parsed insights use only current jobs in the database; orphaned parsed payloads from purged jobs are still counted in backend stats for transparency but are excluded from coverage metrics.
- Parsed insights also surface senior vs non-senior deltas (counts, coverage, avg experience, avg salary) so keep those fields populated when extending the parser or UI.
- Frontend layout: Dashboard and Summary are reachable via the header toggle. Job filters are slimmed to company + sort + clear beneath the records count, and the table shows 5 rows per page without internal scrolling.
- Parsed insights also surface senior vs non-senior deltas (counts, coverage, avg experience, avg salary) so keep those fields populated when extending the parser or UI.
