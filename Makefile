PY ?= $(if $(wildcard ../../.venv/bin/python),../../.venv/bin/python,python3)
GCP_PROJECT ?= your-project-id
GCP_REGION ?= us-central1
BUCKET ?= $(GCP_PROJECT)-refill-artifacts
JOB_NAME ?= refill-chase
SCHEDULER_NAME ?= refill-followup-scheduler

.PHONY: deploy demo job tick test teardown

test:
	PYTHONPATH=.:../shared $(PY) -m pytest tests/ -v

deploy:
	@echo "Refill has no infra/deploy.sh yet (see LIMITATIONS.md 'Build status')."
	@echo "The Cloud Run Job entrypoint itself is real and runnable:"
	@echo "  REFILL_LAST_FILL=2026-08-01 make job"
	@exit 1

job:
	PYTHONPATH=.:../shared $(PY) -m job.main

demo:
	PYTHONPATH=.:../shared $(PY) demo_local.py

tick:
	gcloud run jobs execute $(JOB_NAME) --region $(GCP_REGION) --project $(GCP_PROJECT) --wait

teardown:
	@test -f infra/teardown.sh && bash infra/teardown.sh $(GCP_PROJECT) $(GCP_REGION) $(BUCKET) $(JOB_NAME) $(SCHEDULER_NAME) || echo "nothing deployed yet"
