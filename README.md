# Job Informer

Job Informer is a small job-collection pipeline:

- scrapes fresh job postings (currently LinkedIn focused),
- filters and de-duplicates them,
- optionally enriches with AI parsing,
- exposes results via API/UI,
- and sends email digests.

## Project layout

- `backend/` – scraping, parser, email sender, SQLite persistence, API.
- `frontend/` – UI for starting runs, checking status, and browsing jobs.
- `docker-compose.yml` – single-container local deployment.

## Quickest path (recommended for new users)

1. Install Docker.
2. Create environment file:
   - `cp .env.example .env`
3. Edit `.env` with your credentials (at minimum: `OPENAI_API_KEY`, `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, `RECIPIENT_EMAIL`, and `API_TOKEN` if you want protected API calls).
   - `backend/data/jobs.db` is mounted into the container, so existing data in this file will be used automatically.
4. Run:
   - `docker compose up --build`
5. Open:
   - App + API: `http://localhost:8000`
   - Health check: `http://localhost:8000/health`

> If you do not run Docker, use the local install path below.

## Local setup (backend + frontend)

### Backend

```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
pip install -r backend/requirements.txt
python backend/main.py --run-once --config .env
```

Start API:

```bash
uvicorn backend.api:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env  # set VITE_API_BASE to http://localhost:8000 for local Vite dev
npm run dev
```

Frontend will be served by Vite on `http://localhost:5173` in local mode.

## Environment file (ambiguous gotchas)

### Required for a full run

`OPENAI_API_KEY`, `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, `RECIPIENT_EMAIL`.

### Recommended

`API_TOKEN`, `SEARCH_KEYWORDS`, `SEARCH_LOCATIONS`.

### Common config values

- `SMTP_SERVER` (default: `smtp.gmail.com`)
- `SMTP_PORT` (default: `587`)
- `OPENAI_MODEL` (default: `gpt-5-mini`)
- `DESC_PARSER_MODEL` (defaults to `OPENAI_MODEL`)
- `ENABLE_DESCRIPTION_PARSER` (`true`/`false`)
- `ENABLE_LINKEDIN` (`true`/`false`, default: `true`)
- `ENABLE_INDEED` (`true`/`false`, default: `false`)
- `SCRAPE_DESCRIPTIONS` (`true`/`false`, default: `true`) — fetch full descriptions during `--run-once`
- `SCRAPE_DESCRIPTIONS_LIMIT` (integer, default: `20`) — max number of scraped jobs to fetch descriptions for per run
- `INDEED_MAX_SEARCH_PAGES` (`0` means default page math, otherwise explicit source page limit)
- `INDEED_SESSION_COOKIES` (cookie string copied from an active indeed browser session, optional)
- `SEARCH_KEYWORDS` (comma-separated list)
- `SEARCH_LOCATIONS` (comma-separated list)
- `SEARCH_TIME_RANGE` (`day`, `week`, `month`)
- `REQUEST_DELAY` (seconds between requests)
- `MAX_RETRIES` (HTTP retry count)
- `MAX_TOTAL_JOBS` (`0` means unlimited)
- `MIN_NEW_JOBS_TO_CONTINUE` (run-stop threshold)
- `LOG_LEVEL` (`INFO`, `DEBUG`, ...)
- `VITE_API_BASE` (backend URL for frontend; defaults to `/` in Docker and should be set to `http://localhost:8000` for local Vite dev)
- `VITE_API_TOKEN` (same token as `API_TOKEN` if token auth is enabled)
- `JOBS_DB_PATH` (usually set automatically by compose to `/app/data/jobs.db`).

The config is loaded from repo-root `.env` automatically.  
You can also pass an explicit file path:

```bash
python backend/main.py --run-once --config path/to/.env
```

## CLI reference

Run one of these modes (mutually exclusive):

- `--run-once` (default): scrape and save jobs
- `--test`: run sanity checks
- `--db-summary`: print DB summary
- `--purge`: apply rule-based + AI purge pipeline
- `--parse-descriptions`: fetch missing descriptions and parse with AI
- `--refetch-titles`: recover masked titles/companies from source pages
- `--reset-ai-purge`: clear AI purge state
- `--inform [last|random]`: send email digest from DB
- `--probe-indeed`: run a one-off Indeed query probe without storing results (great for endpoint checks)

### Useful overrides

```bash
python backend/main.py --run-once --keywords "Data Scientist" --locations "Berlin" --time week
python backend/main.py --parse-descriptions
python backend/main.py --purge
python backend/main.py --probe-indeed --keywords "Data" --locations "Deutschland"
```

## API quick reference

Most frontend behavior is behind these endpoints.

- `GET /health` health check.
- `POST /runs` trigger async mode.
- `GET /runs` list recent run summaries.
- `GET /runs/{run_id}` poll one run.
- `GET /jobs` list jobs (pagination + filters).
- `GET /jobs/{job_id}` read one job (with parsed details when present).
- `DELETE /jobs/{job_id}` remove a job.
- `GET /stats` dashboard stats.
- `GET /db-summary` richer backend summary.

Example run trigger:

```bash
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -H "x-api-token: $API_TOKEN" \
  -d '{"mode":"run-once","keywords":"Data Scientist","locations":"Berlin, Stuttgart","time_range":"week"}'
```

Example job query:

```bash
curl "http://localhost:8000/jobs?limit=20&sort=scraped_at_desc&search=engineer&location=Berlin"
```

If `API_TOKEN` is empty, you can omit the `x-api-token` header.

## Docker specifics

- Single compose file: `docker-compose.yml`.
- Backend and frontend are now in a single service (`backend`), sharing one container, one port.
- Backend runs with persisted volumes:
  - `./backend/data` (bind-mounted) → `/app/data`
  - `job-logs` → `/app/logs`

Recommended startup:

```bash
docker compose up --build
```

To stop:

```bash
docker compose down
```

## Data and logs

- Database: `backend/data/jobs.db` (or `JOBS_DB_PATH` when customized).
- Parsed description cache is stored in DB tables used by both parser and UI.
- Logs: `/app/logs/job_informer.log` in the container (`job-logs` volume in Docker), or the path in `LOG_FILE` if customized.

## Newcomer troubleshooting

- `No jobs found`:
  - Check `SEARCH_KEYWORDS`/`SEARCH_LOCATIONS`.
  - Try a broader `SEARCH_TIME_RANGE`.
  - LinkedIn can throttle requests; the scraper uses retries/backoff and may return fewer results.
- `401` from API:
  - `API_TOKEN` is set but `x-api-token` header is missing/incorrect.
- SMTP/auth errors:
  - Confirm `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, and `RECIPIENT_EMAIL` are correct.
- Docker container exits quickly:
  - Verify `.env` exists at repository root (compose reads root `.env`).

## Contributor notes

- The API exposes the same operations via `/runs` mode values (`run-once`, `purge`, `parse-descriptions`, etc.).
