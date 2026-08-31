#!/usr/bin/env bash
#
# infra/teardown.sh — deletes everything infra/deploy.sh created, in reverse
# order, so a judge can spin the demo up and take it back down without
# leaving billable resources behind.
#
# Usage:
#   bash infra/teardown.sh PROJECT_ID [REGION] [BUCKET] [CHASE_JOB] [SCHEDULER_NAME]
# or via make:
#   make teardown PROJECT_ID=my-gcp-project
#
# The artifact bucket is NOT deleted automatically: it holds the packet
# artifacts that are the evidence of what ran. The exact command to remove it
# is printed at the end.

set -euo pipefail

PROJECT_ID="${1:-${PROJECT_ID:?set PROJECT_ID}}"
REGION="${2:-${REGION:-us-central1}}"
BUCKET="${3:-${BUCKET:-${PROJECT_ID}-refill-artifacts}}"
CHASE_JOB="${4:-refill-chase}"
SCHEDULER_NAME="${5:-refill-followup-scheduler}"
FOLLOWUP_JOB="${FOLLOWUP_JOB:-refill-followup}"

step() { printf '  -- %s\n' "$*"; }

command -v gcloud >/dev/null 2>&1 || { echo "FATAL: gcloud not on PATH" >&2; exit 1; }

echo "== Refill teardown: project=${PROJECT_ID} region=${REGION} =="

step "deleting Cloud Scheduler job ${SCHEDULER_NAME}"
gcloud scheduler jobs delete "${SCHEDULER_NAME}" \
  --project "${PROJECT_ID}" --location "${REGION}" --quiet 2>/dev/null \
  || step "already gone, skipping"

for job in "${FOLLOWUP_JOB}" "${CHASE_JOB}"; do
  step "deleting Cloud Run Job ${job}"
  gcloud run jobs delete "${job}" \
    --project "${PROJECT_ID}" --region "${REGION}" --quiet 2>/dev/null \
    || step "already gone, skipping"
done

cat <<EOF

Teardown complete. Scheduler and both Cloud Run Jobs are deleted.

The artifact bucket is intentionally left in place so the packets written
during the demo remain inspectable. Delete it yourself with:

  gcloud storage rm -r gs://${BUCKET} --project ${PROJECT_ID}

The service accounts (refill-job-sa, refill-scheduler-sa) are also left in
place, since they are cheap and shared with any other deploy of this project.
EOF
