#!/usr/bin/env bash
#
# deploy_refill.sh — build and deploy Refill to Cloud Run.
#
# Usage:
#   PROJECT_ID=my-gcp-project REGION=us-central1 bash infra/deploy.sh
#
# Positional form used by `make deploy`:
#   bash infra/deploy.sh PROJECT_ID REGION BUCKET CHASE_JOB SCHEDULER_NAME
#
# Optional:
#   SCHEDULE="*/2 * * * *"   filming cadence; production is "0 9 * * *" (daily)
#   IMAGE_TAG=v1             defaults to a git-sha tag
#   REFILL_LAST_FILL=...     ISO date seeded into the chase job's env
#
# Prerequisite: the two service accounts below exist in PROJECT_ID (see README
# "One-command deploy" for the exact gcloud commands that create them).
#
# What it deploys, from ONE image:
#   1. refill-chase     Cloud Run Job, REFILL_MODE=chase    — validate + issue packet
#   2. refill-followup  Cloud Run Job, REFILL_MODE=followup — the later tick that
#                       appends the genuinely time-delayed second log entry
#   3. refill-followup-scheduler  Cloud Scheduler -> refill-followup
#
# The chase job is NOT on a schedule. It is triggered by the caregiver
# finishing the dialogue, which is a human act. Only the follow-up is
# scheduled, and that follow-up is precisely the part that outlives the
# request (specs/02: "Scheduler tick later: still pending? draft follow-up").
# Scheduling the chase itself would be background-execution theatre.

set -euo pipefail

PROJECT_ID="${1:-${PROJECT_ID:?set PROJECT_ID}}"
REGION="${2:-${REGION:-us-central1}}"
ARTIFACT_REPO="${ARTIFACT_REPO:-agents}"
SCHEDULE="${SCHEDULE:-*/2 * * * *}"
BUCKET="${3:-${BUCKET:-${PROJECT_ID}-refill-artifacts}}"

CHASE_JOB="${4:-refill-chase}"
FOLLOWUP_JOB="refill-followup"
SCHEDULER_NAME="${5:-refill-followup-scheduler}"

JOB_SA="refill-job-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="refill-scheduler-sa@${PROJECT_ID}.iam.gserviceaccount.com"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# This repo is standalone (it vendors agentspine), so the build context is
# the repo root itself.
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

log()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
step() { printf '  -- %s\n' "$*"; }
die()  { printf '\n\033[31mFATAL: %s\033[0m\n' "$*" >&2; exit 1; }

command -v gcloud >/dev/null 2>&1 || die "gcloud not on PATH. See PREFLIGHT.md step 0."
[[ -f "${REPO_ROOT}/Dockerfile" ]] || die "cannot find ${REPO_ROOT}/Dockerfile"

gcloud iam service-accounts describe "${JOB_SA}" --project "${PROJECT_ID}" >/dev/null 2>&1 \
  || die "service account ${JOB_SA} missing. See README 'One-command deploy'."

if [[ -z "${IMAGE_TAG:-}" ]]; then
  IMAGE_TAG="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo latest)"
fi
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/refill:${IMAGE_TAG}"

# Demo case defaults. last_fill + days_supply drive the deterministic
# calculator; leaving REFILL_MODEL_CLAIMED_DATE unset means "model agrees",
# which is the happy path. Set a wrong claimed date to
# force the on-camera REJECTED run.
REFILL_LAST_FILL="${REFILL_LAST_FILL:-2026-08-01}"
REFILL_DAYS_SUPPLY="${REFILL_DAYS_SUPPLY:-30}"
REFILL_PLAN="${REFILL_PLAN:-SamplePlan-PPO}"
REFILL_USER_ID="${REFILL_USER_ID:-demo-caregiver}"

log "Refill deploy: project=${PROJECT_ID} region=${REGION} tag=${IMAGE_TAG}"

# ---------------------------------------------------------------------------
# 1. Build once, deploy twice.
# ---------------------------------------------------------------------------
log "1/3 build ${IMAGE}"
step "build context is this repo root; agentspine is vendored in-repo"
CLOUDBUILD_CFG="$(mktemp -t refill-cloudbuild-XXXXXX.yaml)"
trap 'rm -f "${CLOUDBUILD_CFG}"' EXIT
cat > "${CLOUDBUILD_CFG}" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args: ["build", "-f", "Dockerfile", "-t", "${IMAGE}", "."]
images: ["${IMAGE}"]
options:
  logging: CLOUD_LOGGING_ONLY
EOF
gcloud builds submit "${REPO_ROOT}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --config "${CLOUDBUILD_CFG}"

# ---------------------------------------------------------------------------
# 2. The two jobs. No API key anywhere: GOOGLE_GENAI_USE_VERTEXAI=TRUE routes
#    the model call through Vertex AI using the job's own service account.
# ---------------------------------------------------------------------------
COMMON_ENV="REFILL_BUCKET=${BUCKET}##REFILL_USE_FIRESTORE=1##GOOGLE_CLOUD_PROJECT=${PROJECT_ID}##GOOGLE_CLOUD_LOCATION=${REGION}##GOOGLE_GENAI_USE_VERTEXAI=TRUE"

log "2/3 Cloud Run Jobs"
step "deploying ${CHASE_JOB} (REFILL_MODE=chase)"
gcloud run jobs deploy "${CHASE_JOB}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE}" \
  --service-account "${JOB_SA}" \
  --set-env-vars "^##^${COMMON_ENV}##REFILL_MODE=chase##REFILL_USER_ID=${REFILL_USER_ID}##REFILL_PLAN=${REFILL_PLAN}##REFILL_LAST_FILL=${REFILL_LAST_FILL}##REFILL_DAYS_SUPPLY=${REFILL_DAYS_SUPPLY}" \
  --max-retries 1 \
  --task-timeout 5m \
  --cpu 1 --memory 1Gi \
  --quiet

step "deploying ${FOLLOWUP_JOB} (REFILL_MODE=followup)"
# REFILL_RUN_ID is set per-execution with --update-env-vars at run time.
# A follow-up with no run id is a clean no-op exit 0, not a crash.
gcloud run jobs deploy "${FOLLOWUP_JOB}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE}" \
  --service-account "${JOB_SA}" \
  --set-env-vars "^##^${COMMON_ENV}##REFILL_MODE=followup" \
  --max-retries 1 \
  --task-timeout 5m \
  --cpu 1 --memory 1Gi \
  --quiet

# ---------------------------------------------------------------------------
# 3. Scheduler -> follow-up job only.
# ---------------------------------------------------------------------------
log "3/3 Cloud Scheduler ${SCHEDULER_NAME}"
step "granting run.invoker on ${FOLLOWUP_JOB} to ${SCHEDULER_SA}"
gcloud run jobs add-iam-policy-binding "${FOLLOWUP_JOB}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --member "serviceAccount:${SCHEDULER_SA}" \
  --role "roles/run.invoker" \
  --quiet >/dev/null

JOB_RUN_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${FOLLOWUP_JOB}:run"

if gcloud scheduler jobs describe "${SCHEDULER_NAME}" \
     --project "${PROJECT_ID}" --location "${REGION}" >/dev/null 2>&1; then
  step "scheduler exists, updating schedule to '${SCHEDULE}'"
  gcloud scheduler jobs update http "${SCHEDULER_NAME}" \
    --project "${PROJECT_ID}" --location "${REGION}" \
    --schedule "${SCHEDULE}" --uri "${JOB_RUN_URI}" --http-method POST \
    --oauth-service-account-email "${SCHEDULER_SA}" --quiet
else
  step "creating scheduler at '${SCHEDULE}'"
  gcloud scheduler jobs create http "${SCHEDULER_NAME}" \
    --project "${PROJECT_ID}" --location "${REGION}" \
    --schedule "${SCHEDULE}" --uri "${JOB_RUN_URI}" --http-method POST \
    --oauth-service-account-email "${SCHEDULER_SA}" --quiet
fi

# Why --oauth- and not --oidc-: this is --oauth- and not --oidc-: the target
# is the Cloud Run Admin API, a Google API, which takes an OAuth access token.

log "Refill deploy complete"
cat <<EOF

  Chase job:     ${CHASE_JOB}      (${REGION}, run on demand)
  Follow-up job: ${FOLLOWUP_JOB}   (${REGION}, scheduled '${SCHEDULE}')
  Artifacts:     gs://${BUCKET}/chase/

  Force the on-camera REJECTED run (model date contradicts the calculator):
    gcloud run jobs execute ${CHASE_JOB} --region ${REGION} --project ${PROJECT_ID} \\
      --update-env-vars REFILL_MODEL_CLAIMED_DATE=2026-08-10 --wait

  Console links (the screens to film):
    Jobs       https://console.cloud.google.com/run/jobs?project=${PROJECT_ID}
    Executions https://console.cloud.google.com/run/jobs/details/${REGION}/${CHASE_JOB}/executions?project=${PROJECT_ID}
    Scheduler  https://console.cloud.google.com/cloudscheduler?project=${PROJECT_ID}
    Trace      https://console.cloud.google.com/traces/list?project=${PROJECT_ID}
    Bucket     https://console.cloud.google.com/storage/browser/${BUCKET}?project=${PROJECT_ID}

  Production cadence for the follow-up is daily ("0 9 * * *"); ${SCHEDULE} is for filming.
EOF
