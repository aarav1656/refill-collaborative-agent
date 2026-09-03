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

Chase mode invokes the real ADK dialogue agent (`agent/refill_agent.py`'s
`LlmAgent`, run the same way `agent/run_chat.py`'s CLI already runs it: a
single turn through an `InMemoryRunner`) so the date `job/tick.py`'s
validator adjudicates is the MODEL'S OWN proposal from the
`propose_next_eligible_date` tool call, not a value only a human ever
typed into an env var. This is the same "wire the real agent into the job
entrypoint" fix `projects/sovereign/job/main.py` already applied for its
`build_fleet()` fleet -- see that module's docstring for the reference
pattern. Requires GOOGLE_API_KEY (or GEMINI_API_KEY); `_run_dialogue_agent`
calls `agent.run_chat.require_api_key()` first and raises loudly with no
fallback if neither is set, matching `agent/run_chat.py` and Sovereign's
`agent/tools.py`'s "never fabricate" rule. Follow-up mode makes no model
call and has no key requirement.

The calculator stays authoritative regardless of what the model claims:
`job/tick.py`'s `_BoundEligibilityValidator` recomputes
`validator.eligibility.compute_next_eligible` itself from this job's own
trusted `ChaseInput` fields (never from anything the model said), and
compares that to the model's claimed date. A model disagreement still
blocks the packet exactly as before this change.

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
    REFILL_MODEL_CLAIMED_DATE  optional ISO date. Chase mode always asks
                           the real ADK agent to propose a date; this
                           value, when set, is passed to the agent as its
                           WORKING ESTIMATE hint so the on-camera "force a
                           wrong date" demo case (see
                           `infra/deploy.sh`) still reliably reproduces a
                           DISAGREE -- but the value that actually reaches
                           the validator is still whatever the model
                           relays back through `propose_next_eligible_date`,
                           not this env var directly. When this disagrees
                           with the calculator the run is REJECTED and no
                           packet is written.
    REFILL_WINDOW          idempotency window; defaults to REFILL_LAST_FILL
                           so two ticks for the same fill collapse onto one
                           run_id (never wall-clock time).
    REFILL_RUN_ID          followup mode only: which run to append to.
                           unset -> clean no-op, exit 0 (the Scheduler's
                           unattended cadence ticks never set this).
    REFILL_FOLLOWUP_TEXT   followup mode only: the drafted text.

No secrets are read from disk and none are baked into the image; GCP access
comes from Application Default Credentials via the job's service account.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from typing import Optional

from agentspine.artifacts import ArtifactBackend, GcsBackend, LocalBackend
from agentspine.idempotency import FirestoreBackend, IdempotencyBackend, MemoryBackend

from agent.refill_agent import ProposalRecord
from agent.run_chat import require_api_key, run_single_turn
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
        model_claimed_date=None,  # filled in by _run_dialogue_agent, below
        window_start=os.environ.get("REFILL_WINDOW") or last_fill_raw,
    )


def _dialogue_prompt(chase: ChaseInput, override_hint: Optional[date]) -> str:
    """Build the opening message the ADK agent sees for this chase.

    States the fields job/main.py already trusts (from env) directly,
    rather than making the model re-derive them from a denial letter --
    that extraction is `agent/denial_letter.py`'s job, upstream of this
    job entrypoint. `override_hint`, when set (REFILL_MODEL_CLAIMED_DATE),
    is passed along as a working estimate so the demo's "force a wrong
    date" case still reproduces a real DISAGREE from a real tool call,
    instead of bypassing the agent.
    """
    prompt = (
        f"A caregiver's denial letter: medication={chase.medication}, "
        f"last fill date={chase.last_fill_date.isoformat()}, "
        f"days supply={chase.days_supply}, plan={chase.plan}, "
        f"denial reason={chase.denial_reason!r}. Compute the next-eligible "
        "refill date now and call propose_next_eligible_date with your "
        "best-guess model_claimed_date."
    )
    if override_hint is not None:
        prompt += (
            f" Our working estimate for the next-eligible date is "
            f"{override_hint.isoformat()}; use that as your "
            "model_claimed_date argument when you call the tool."
        )
    return prompt


def _run_dialogue_agent(
    chase: ChaseInput, override_hint: Optional[date]
) -> tuple[Optional[date], list[ProposalRecord], str]:
    """Invoke the real ADK `LlmAgent` for one turn on the chase's fields.

    Runs the exact same `build_refill_agent` + `InMemoryRunner` wiring
    `agent/run_chat.py`'s manual CLI already uses (via
    `run_single_turn`), so this job entrypoint and the CLI share one
    runner implementation rather than the job reimplementing its own.
    `require_api_key()` is called first and raises `SystemExit` loudly,
    naming the missing env var, if no GOOGLE_API_KEY/GEMINI_API_KEY is
    set -- no offline fallback on this path.

    Returns (model_claimed_date parsed from the tool's log, the
    proposal log, the agent's final text reply). model_claimed_date is
    None if the agent never called propose_next_eligible_date (a
    legitimate outcome: the calculator veto's "no competing claim"
    branch handles it, exactly as it does for a human-typed opening
    with no model involved).
    """
    require_api_key()
    proposal_log: list[ProposalRecord] = []
    prompt = _dialogue_prompt(chase, override_hint)
    reply = asyncio.run(run_single_turn(prompt, chase.user_id, proposal_log))

    claimed: Optional[date] = None
    if proposal_log:
        try:
            claimed = date.fromisoformat(proposal_log[-1].model_claimed_date)
        except (TypeError, ValueError):
            claimed = None
    return claimed, proposal_log, reply


def main() -> int:
    idempotency, artifacts = _backends()
    mode = os.environ.get("REFILL_MODE", "chase")

    if mode == "followup":
        run_id = os.environ.get("REFILL_RUN_ID")
        if not run_id:
            # The Scheduler fires this job on a fixed cadence with no
            # REFILL_RUN_ID -- it is only set via --update-env-vars for a
            # specific on-camera follow-up. A tick with nothing to follow
            # up on is a legitimate, expected outcome, not a crash: match
            # infra/deploy.sh's documented "clean no-op exit 0" contract
            # instead of raising, so the scheduled ticks that make up most
            # of this job's executions don't paint the Cloud Run Jobs
            # console red with false failures.
            print("mode=followup run_id=(none) log_uri=no-op (no REFILL_RUN_ID set)")
            return 0
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
    override_hint = _opt_date("REFILL_MODEL_CLAIMED_DATE")
    claimed, proposal_log, reply = _run_dialogue_agent(chase, override_hint)
    chase.model_claimed_date = claimed

    print(f"agent> {reply}")
    for record in proposal_log:
        print(
            f"  model_claimed={record.model_claimed_date} "
            f"calculator={record.calculator_date} agreed={record.agreed}"
        )

    result = run_chase_tick(chase, idempotency, artifacts)
    print(f"mode=chase run_id={result.run_id} status={result.status}")
    if result.artifact_uri:
        print(f"artifact_uri={result.artifact_uri}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
