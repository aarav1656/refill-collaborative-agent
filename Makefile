PY ?= python3
GCP_PROJECT ?= $(error set GCP_PROJECT)
GCP_REGION ?= us-central1
BUCKET ?= $(GCP_PROJECT)-refill-artifacts
JOB_NAME ?= refill-chase
SCHEDULER_NAME ?= refill-followup-scheduler

.PHONY: deploy demo chase tick forget test teardown

test:
	$(PY) -m pytest tests/ -v

deploy:
	@echo "infra/deploy.sh not yet implemented; see LIMITATIONS.md."
	@test -f infra/deploy.sh && bash infra/deploy.sh $(GCP_PROJECT) $(GCP_REGION) $(BUCKET) $(JOB_NAME) $(SCHEDULER_NAME) || exit 1

demo:
	@test -f infra/demo.sh && bash infra/demo.sh $(GCP_PROJECT) $(GCP_REGION) $(JOB_NAME) $(BUCKET) || echo "demo.sh pending, run 'make test' for the validator/calculator today"

chase:
	@test -f job/main.py && $(PY) -m job.main --letter $(LETTER) --last-fill $(LAST_FILL) || echo "job/main.py pending"

tick:
	gcloud run jobs execute $(JOB_NAME) --region $(GCP_REGION) --project $(GCP_PROJECT) --wait

forget:
	@test -f memory/__init__.py -a -s memory/__init__.py && $(PY) -m memory forget --profile $(PROFILE) --fact $(FACT) || echo "memory/ pending"

teardown:
	@test -f infra/teardown.sh && bash infra/teardown.sh $(GCP_PROJECT) $(GCP_REGION) $(BUCKET) $(JOB_NAME) $(SCHEDULER_NAME) || echo "nothing deployed yet"
