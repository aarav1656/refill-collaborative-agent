# Refill Cloud Run Job container.
#
# Build context is THIS repo root: `docker build -t refill .`
# The repo vendors agentspine, so no sibling directory is needed and the
# image builds from a clean clone of just this repo.
#
# No secrets are baked in. Configuration comes from environment variables on
# the Cloud Run Job; GCP credentials come from Application Default
# Credentials via the job's attached service account.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

RUN useradd --create-home --uid 1001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# REFILL_MODE (chase|followup) selects which tick runs. Both are set on the
# Cloud Run Job by infra/deploy.sh, which creates two jobs sharing this one
# image.
CMD ["python", "-m", "job.main"]
