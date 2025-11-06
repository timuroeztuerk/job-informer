# Job Informer

Job Informer scrapes fresh data-focused job postings, filters them with lightweight LLM heuristics, and delivers concise email digests.

## Quick Start
- Ensure Python 3.11+ is installed.
- Install dependencies: `pip install -r requirements.txt`
- Copy `.env.example` → `.env` and fill in credentials, especially `OPENAI_API_KEY` and email SMTP settings.
- Run a one-off scrape: `python main.py --run-once`

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
