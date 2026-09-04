.PHONY: up doctor test backup relevance-preview relevance-apply restore

up:
	docker compose up -d --build
	python3 backend/maintenance.py doctor

doctor:
	python3 backend/maintenance.py doctor

test:
	docker compose build backend
	docker compose run --rm --no-deps \
		-e JOBS_DB_PATH=/tmp/job-informer-tests.db \
		-e RUN_STORE_DB_PATH=/tmp/job-informer-tests.db \
		backend python -m unittest discover -s backend/tests -t .
	docker build --target frontend-build -t job-informer-frontend-test -f backend/Dockerfile .
	docker run --rm job-informer-frontend-test npm test -- --run

backup:
	docker compose exec backend python backend/maintenance.py backup

relevance-preview:
	docker compose exec backend python backend/maintenance.py relevance-preview

relevance-apply:
	@test "$(CONFIRM)" = "yes" || (echo "Run with CONFIRM=yes after reviewing relevance-preview"; exit 1)
	docker compose exec backend python backend/maintenance.py relevance-apply --confirm $(if $(ARCHIVE_UNMATCHED),--include-unmatched,)

restore:
	@test -n "$(BACKUP)" || (echo "Run with BACKUP=/app/data/backups/<file>.db"; exit 1)
	@test "$(CONFIRM)" = "yes" || (echo "Run with CONFIRM=yes after stopping writes"; exit 1)
	docker compose exec backend python backend/maintenance.py restore "$(BACKUP)" --confirm
