PYTHON ?= python

.PHONY: up doctor test test-image

up:
	docker compose up -d --build
	$(PYTHON) scripts/doctor.py

doctor:
	$(PYTHON) scripts/doctor.py

test:
	$(PYTHON) -m unittest discover -s backend/tests -t .
	cd frontend && npm test -- --run

test-image:
	docker compose build backend
	docker compose run --rm --no-deps \
		-e JOBS_DB_PATH=/tmp/job-informer-tests.db \
		-e RUN_STORE_DB_PATH=/tmp/job-informer-tests.db \
		backend python -m unittest discover -s backend/tests -t .
