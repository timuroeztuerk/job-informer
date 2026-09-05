BACKEND_TEST_IMAGE := job-informer-backend-test:$(shell git hash-object backend/Dockerfile backend/requirements.txt | git hash-object --stdin | cut -c1-12)
FRONTEND_TEST_IMAGE := job-informer-frontend-test:$(shell git hash-object backend/Dockerfile frontend/package.json frontend/package-lock.json | git hash-object --stdin | cut -c1-12)

# Run only the relevant module/file during iteration; `make test` still runs everything.
# make test-backend BACKEND_TESTS=backend.tests.test_job_flags
# make test-frontend FRONTEND_TESTS=JobDetail
BACKEND_TESTS ?=
FRONTEND_TESTS ?=
BACKEND_TEST_ARGS ?=
FRONTEND_TEST_ARGS ?=

# Real SQLite files/WAL on disposable RAM storage avoid Docker layer fsync overhead.
BACKEND_TEST_COMMAND = docker run --rm --network none --tmpfs /tmp:rw,nosuid,nodev,size=256m \
	-e JOBS_DB_PATH=/tmp/job-informer-tests.db -e RUN_STORE_DB_PATH=/tmp/job-informer-tests.db -e LOGURU_LEVEL=ERROR \
	-v "$(CURDIR)/backend:/app/backend:ro" $(BACKEND_TEST_IMAGE) \
	python -m unittest $(if $(strip $(BACKEND_TESTS)),$(BACKEND_TESTS),discover -s backend/tests -t .) -q $(BACKEND_TEST_ARGS)

# Cache dependencies only. Always read the current source, tests, and configuration.
FRONTEND_TEST_COMMAND = docker run --rm --network none \
	-v "$(CURDIR)/frontend/src:/frontend/src:ro" -v "$(CURDIR)/frontend/tests:/frontend/tests:ro" \
	-v "$(CURDIR)/frontend/vite.config.ts:/frontend/vite.config.ts:ro" \
	-v "$(CURDIR)/frontend/tsconfig.json:/frontend/tsconfig.json:ro" \
	-v "$(CURDIR)/frontend/tsconfig.node.json:/frontend/tsconfig.node.json:ro" \
	-v "$(CURDIR)/frontend/tsconfig.test.json:/frontend/tsconfig.test.json:ro" \
	$(FRONTEND_TEST_IMAGE) node node_modules/vitest/vitest.mjs run --reporter=dot $(FRONTEND_TESTS) $(FRONTEND_TEST_ARGS)

.PHONY: up doctor test test-backend test-frontend backend-test-image frontend-test-image backup relevance-preview relevance-apply restore

up:
	docker compose up -d --build
	python3 backend/maintenance.py doctor

doctor:
	python3 backend/maintenance.py doctor

test:
	@$(MAKE) --no-print-directory -j2 backend-test-image frontend-test-image
	@status=0; \
		$(BACKEND_TEST_COMMAND) & backend_pid=$$!; \
		$(FRONTEND_TEST_COMMAND) & frontend_pid=$$!; \
		wait $$backend_pid || status=1; \
		wait $$frontend_pid || status=1; \
		exit $$status

test-backend: backend-test-image
	$(BACKEND_TEST_COMMAND)

test-frontend: frontend-test-image
	$(FRONTEND_TEST_COMMAND)

backend-test-image:
	@docker image inspect $(BACKEND_TEST_IMAGE) >/dev/null 2>&1 || \
		docker build --target backend-test -t $(BACKEND_TEST_IMAGE) -f backend/Dockerfile .

frontend-test-image:
	@docker image inspect $(FRONTEND_TEST_IMAGE) >/dev/null 2>&1 || \
		docker build --target frontend-test -t $(FRONTEND_TEST_IMAGE) -f backend/Dockerfile .

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
