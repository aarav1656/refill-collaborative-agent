"""job/main.py — Cloud Run Job entrypoint for Refill.

This is deliberately thin. All logic lives in `job/tick.py`
(`run_chase_tick`, `append_followup_entry`), which is what the offline test
suite exercises. This module does exactly three things:

    1. read configuration from environment variables set on the Cloud Run Job,
    2. pick real GCP backends (Firestore + GCS) or local ones,
    3. call the existing tick function and print the result.

Two modes, selected by REFILL_MODE:

    REFILL_MODE=chase    (default) run one chase tick: validate the
                         eligibility arithmetic, and on PASS write
                         chase/<run_id>/packet.pdf + log.jsonl.
    REFILL_MODE=followup run the later tick that appends the genuinely
                         time-delayed second entry to log.jsonl. This is
                         the part that outlives the HTTP request, per
                         specs/02-refill-collaborative.md.

Environment variables (all optional except where noted):

    REFILL_MODE            chase | followup            (default chase)
    REFILL_BUCKET          GCS bucket name; unset -> local artifacts dir
    REFILL_USE_FIRESTORE   "1" -> Firestore idempotency; else in-memory
    REFILL_LOCAL_ARTIFACT_DIR  local dir when REFILL_BUCKET is unset
    REFILL_USER_ID         chase subject / idempotency subject
    REFILL_MEDICATION, REFILL_NDC, REFILL_PLAN
    REFILL_LAST_FILL       ISO date, e.g. 2026-08-01
    REFILL_DAYS_SUPPLY     int
    REFILL_DENIAL_REASON
    REFILL_MODEL_CLAIMED_DATE  ISO date the model asserted; when this
                           disagrees with the calculator the run is
                           REJECTED and no packet is written. This is the
                           on-camera "validator has veto" case.
    REFILL_WINDOW          idempotency window; defaults to REFILL_LAST_FILL
                           so two ticks for the same fill collapse onto one
                           run_id (never wall-clock time).
    REFILL_RUN_ID          followup mode only: which run to append to.
    REFILL_FOLLOWUP_TEXT   followup mode only: the drafted text.

No secrets are read from disk and none are baked into the image; GCP access
comes from Application Default Credentials via the job's service account.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from typing import Optional

from agentspine.artifacts import ArtifactBackend, GcsBackend, LocalBackend
from agentspine.idempotency import FirestoreBackend, IdempotencyBackend, MemoryBackend

from job.tick import ChaseInput, append_followup_entry, run_chase_tick


def _backends() -> tuple[IdempotencyBackend, ArtifactBackend]:
    """Real GCP backends when configured, local ones otherwise, so this
    module can be smoke-tested with zero GCP credentials."""
    bucket = os.environ.get("REFILL_BUCKET")
    artifacts: ArtifactBackend
    if bucket:
        artifacts = GcsBackend(bucket)
    else:
        artifacts = LocalBackend(os.environ.get("REFILL_LOCAL_ARTIFACT_DIR"))

    idempotency: IdempotencyBackend
    if os.environ.get("REFILL_USE_FIRESTORE") == "1":
        idempotency = FirestoreBackend()
    else:
        idempotency = MemoryBackend()

    return idempotency, artifacts


def _opt_date(name: str) -> Optional[date]:
    raw = os.environ.get(name)
    return date.fromisoformat(raw) if raw else None


def _chase_from_env() -> ChaseInput:
    last_fill_raw = os.environ.get("REFILL_LAST_FILL")
    if not last_fill_raw:
        raise SystemExit("REFILL_LAST_FILL is required in chase mode (ISO date)")
    last_fill = date.fromisoformat(last_fill_raw)

    return ChaseInput(
        user_id=os.environ.get("REFILL_USER_ID", "demo-caregiver"),
        medication=os.environ.get("REFILL_MEDICATION", "Sample Specialty Med 40mg"),
        ndc=os.environ.get("REFILL_NDC") or None,
        plan=os.environ.get("REFILL_PLAN", "SamplePlan-PPO"),
        last_fill_date=last_fill,
        days_supply=int(os.environ.get("REFILL_DAYS_SUPPLY", "30")),
        denial_reason=os.environ.get("REFILL_DENIAL_REASON", "refill too soon"),
        model_claimed_date=_opt_date("REFILL_MODEL_CLAIMED_DATE"),
        window_start=os.environ.get("REFILL_WINDOW") or last_fill_raw,
    )


def main() -> int:
    idempotency, artifacts = _backends()
    mode = os.environ.get("REFILL_MODE", "chase")

    if mode == "followup":
        run_id = os.environ.get("REFILL_RUN_ID")
        if not run_id:
            raise SystemExit("REFILL_RUN_ID is required in followup mode")
        text = os.environ.get(
            "REFILL_FOLLOWUP_TEXT",
            "No payer response yet. Call the plan and read the phone script in packet.pdf.",
        )
        log_uri = append_followup_entry(run_id, idempotency, artifacts, text)
        # None is a legitimate, idempotent outcome (already followed up, or
        # the run never completed). Say so rather than exiting non-zero.
        print(f"mode=followup run_id={run_id} log_uri={log_uri or 'no-op'}")
        return 0

    if mode != "chase":
        raise SystemExit(f"unknown REFILL_MODE={mode!r} (expected chase|followup)")

    chase = _chase_from_env()
    result = run_chase_tick(chase, idempotency, artifacts)
    print(f"mode=chase run_id={result.run_id} status={result.status}")
    if result.artifact_uri:
        print(f"artifact_uri={result.artifact_uri}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
