"""Cloud Run Job entrypoint for Refill: wires the eligibility validator and
the packet artifact_fn through agentspine.run_tick.

Two tick kinds, matching spec 02's flow:
    - chase tick: runs the validator, and on PASS writes packet.pdf +
      log.jsonl (first entry).
    - follow-up tick: fires later (a separate Scheduler entry / a later
      run of this job with follow_up=True), and if the run is still
      `complete` but has not been followed up, appends a genuinely
      time-delayed second entry to the same log.jsonl. This is the "job
      that outlives the HTTP request" per spec: nothing about this second
      entry can be produced synchronously in the original request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from agentspine import ArtifactBackend, IdempotencyBackend, TickResult, Verdict, run_tick

from artifacts.packet import PacketData, build_packet_pdf
from validator.refill_validator import EligibilityValidator


@dataclass
class ChaseInput:
    """Everything the chase tick needs, already resolved by the dialogue
    flow (agent/dialogue.py) before this tick runs. `model_claimed_date`
    is set by the caller (`job/main.py` populates it from a real ADK
    agent call, `demo_local.py`/tests set it directly) -- `run_chase_tick`
    itself makes no model call, it only re-verifies the arithmetic and
    writes the artifact, matching agentspine's "validator is
    deterministic" rule. The validator recomputes the calculator's answer
    from `last_fill_date`/`days_supply`/`plan` regardless of what
    `model_claimed_date` says, so a model claim can never talk its way
    past the calculator.
    """

    user_id: str
    medication: str
    ndc: Optional[str]
    plan: str
    last_fill_date: date
    days_supply: int
    denial_reason: str
    model_claimed_date: Optional[date]
    window_start: str  # e.g. the chase's creation date, drives run_id


class _BoundEligibilityValidator:
    """Adapts EligibilityValidator (which needs chase-specific fields) to
    agentspine's Validator protocol (whose `context` is just
    subject/window/run_id). Binds the chase's fields via closure and
    merges them into whatever context run_tick passes in, so the
    underlying calculator call is unchanged from validator/refill_validator.py.
    """

    def __init__(self, chase: ChaseInput):
        self._chase = chase
        self._inner = EligibilityValidator()

    def verdict(self, context: dict) -> "Verdict":
        merged = {
            **context,
            "last_fill_date": self._chase.last_fill_date,
            "days_supply": self._chase.days_supply,
            "plan": self._chase.plan,
            "model_claimed_date": self._chase.model_claimed_date,
        }
        return self._inner.verdict(merged)


def run_chase_tick(
    chase: ChaseInput,
    backend: IdempotencyBackend,
    artifacts: ArtifactBackend,
) -> TickResult:
    """One idempotent chase tick: validate, then (on pass) write the packet
    and the first log.jsonl entry."""

    validator = _BoundEligibilityValidator(chase)

    def artifact_fn(context: dict, verdict: "Verdict") -> list[str]:
        run_id = context["run_id"]
        calc_date_str = verdict.evidence["calculator_date"]
        next_eligible = date.fromisoformat(calc_date_str)

        packet = PacketData(
            medication=chase.medication,
            ndc=chase.ndc,
            plan=chase.plan,
            last_fill_date=chase.last_fill_date,
            days_supply=chase.days_supply,
            next_eligible_date=next_eligible,
            denial_reason=chase.denial_reason,
        )
        pdf_bytes = build_packet_pdf(packet)
        packet_uri = artifacts.write(
            f"chase/{run_id}/packet.pdf", pdf_bytes, content_type="application/pdf"
        )

        first_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "packet_issued",
            "run_id": run_id,
            "next_eligible_date": calc_date_str,
        }
        artifacts.write(
            f"chase/{run_id}/log.jsonl",
            json.dumps(first_entry) + "\n",
            content_type="application/jsonl",
        )

        return [packet_uri]

    return run_tick(
        subject=chase.user_id,
        window=chase.window_start,
        validator=validator,
        artifact_fn=artifact_fn,
        backend=backend,
    )


def append_followup_entry(
    run_id: str,
    backend: IdempotencyBackend,
    artifacts: ArtifactBackend,
    followup_text: str,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """The later scheduled tick (spec 02: "Scheduler tick later: still
    pending? draft follow-up, append to log.jsonl").

    Only appends if the run is `complete` (a packet exists) and no
    follow-up has been appended yet -- idempotent the same way run_tick
    is: calling this twice does not duplicate the entry.

    Returns the log path written to, or None if there's nothing to do
    (run not complete, or already followed up).
    """
    record = backend.get(run_id)
    if record is None or record.status != "complete":
        return None

    log_path = f"chase/{run_id}/log.jsonl"
    if artifacts.exists(log_path):
        existing = artifacts.read(log_path).decode("utf-8")
        if "followup_drafted" in existing:
            return None  # already followed up, idempotent no-op
    else:
        existing = ""

    entry = {
        "ts": (now or datetime.now(timezone.utc)).isoformat(),
        "event": "followup_drafted",
        "run_id": run_id,
        "text": followup_text,
    }
    updated = existing + json.dumps(entry) + "\n"
    return artifacts.write(log_path, updated, content_type="application/jsonl")
