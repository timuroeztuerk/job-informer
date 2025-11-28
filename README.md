# Job Informer

Job Informer scrapes fresh data-focused job postings, filters them with lightweight LLM heuristics, and delivers concise email digests.

## Quick Start
- Ensure Python 3.11+ is installed.
- Install dependencies: `pip install -r requirements.txt`
- Copy `.env.example` → `.env` and fill in credentials, especially `OPENAI_API_KEY` and email SMTP settings.
- Run a one-off scrape: `python main.py --run-once`

## Minimal API for the frontend
- Start the API: `uvicorn api:app --reload --port 8000`
- Trigger a scrape: `curl -X POST http://localhost:8000/runs`
- Poll run status: `curl http://localhost:8000/runs/<run_id>`
- Read jobs: `curl 'http://localhost:8000/jobs?limit=20'`
- Single job: `curl http://localhost:8000/jobs/<job_id>`

## Frontend (Vue)
- `cd frontend && npm install`
- Copy `.env.example` → `.env` and set `VITE_API_BASE` (defaults to `http://localhost:8000`).
- Run dev server: `npm run dev` (Vite on port 5173).
- The UI can start runs, watch status, list jobs, and open details/descriptions.

## Common Tasks
- `python main.py --run-once --keywords "Data Scientist" --locations "Berlin"` – scrape with custom filters.
- `python main.py --parse-descriptions` – backfill structured summaries for stored job descriptions.
- `python main.py --ai-purge` – flag irrelevant postings via the configured `gpt-5-mini` model.

## Configuration Highlights
- `OPENAI_MODEL` (default `gpt-5-mini`) drives all LLM calls; override `DESC_PARSER_MODEL` for the parser only.
- Email delivery requires `SMTP_SERVER`, `SMTP_PORT`, `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, and `RECIPIENT_EMAIL`.
- Tune scraping scope with `SEARCH_KEYWORDS`, `SEARCH_LOCATIONS`, and `SEARCH_TIME_RANGE` (`day|week|month`).

## Data & Logs
- SQLite databases live in `data/`; historical scrapes are retained for de-duplication and auditing.
- AI parsing outputs are cached in `parsed_descriptions` to avoid redundant LLM calls.
- Runtime logs stream to `logs/job_informer.log` (level configured via `LOG_LEVEL`).
