# Refill — everything runs from THIS directory. This repo is standalone:
# `pip install -e .` puts agentspine and every dependency in your venv, so no
# PYTHONPATH juggling and no sibling directories are required.
#
#   PY=/path/to/venv/bin/python make test
# or just `make test` after activating the venv.
PY ?= $(if $(wildcard ../../.venv/bin/python),../../.venv/bin/python,python3)

GCP_REGION ?= us-central1
BUCKET ?= $(PROJECT_ID)-refill-artifacts
JOB_NAME ?= refill-chase
SCHEDULER_NAME ?= refill-followup-scheduler

.PHONY: install deploy demo job tick test teardown

install:
	$(PY) -m pip install -e .

test:
	$(PY) -m pytest tests/ -v

demo:
	$(PY) demo_local.py

job:
	$(PY) -m job.main

deploy:
	@test -n "$(PROJECT_ID)" || { echo "usage: make deploy PROJECT_ID=<gcp-project>"; exit 1; }
	bash infra/deploy.sh $(PROJECT_ID) $(GCP_REGION) $(BUCKET) $(JOB_NAME) $(SCHEDULER_NAME)

tick:
	@test -n "$(PROJECT_ID)" || { echo "usage: make tick PROJECT_ID=<gcp-project>"; exit 1; }
	gcloud run jobs execute $(JOB_NAME) --region $(GCP_REGION) --project $(PROJECT_ID) --wait

teardown:
	@test -n "$(PROJECT_ID)" || { echo "usage: make teardown PROJECT_ID=<gcp-project>"; exit 1; }
	bash infra/teardown.sh $(PROJECT_ID) $(GCP_REGION) $(BUCKET) $(JOB_NAME) $(SCHEDULER_NAME)
