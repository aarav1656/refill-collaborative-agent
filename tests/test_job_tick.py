"""End-to-end job tests via job/tick.py + agentspine.run_tick.

This is the file that proves the spec's Definition of Done bullets:
    - Blocked run recorded (model vs calculator disagreement, both values visible)
    - Issued packet recorded
    - Delayed follow-up entry written by a later tick
    - idempotency: exactly one packet for repeated ticks of the same chase
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from agentspine import LocalBackend, MemoryBackend

from job.tick import ChaseInput, append_followup_entry, run_chase_tick


def _sample_chase(model_claimed_date=None) -> ChaseInput:
    return ChaseInput(
        user_id="caregiver-alice",
        medication="Enbrel (etanercept) 50mg/mL",
        ndc="58406-0435-1",
        plan="standard",
        last_fill_date=date(2026, 1, 1),
        days_supply=30,
        denial_reason="Refill too soon.",
        model_claimed_date=model_claimed_date,
        window_start="2026-01-15",  # the denial letter date, drives run_id
    )


class TestDisagreementBlocksPacket:
    def test_model_date_disagrees_with_calculator_produces_no_packet(self):
        backend = MemoryBackend()
        artifacts = LocalBackend()
        chase = _sample_chase(model_claimed_date=date(2026, 2, 15))  # wrong

        result = run_chase_tick(chase, backend, artifacts)

        assert result.status == "rejected"
        assert result.verdict.passed is False
        assert artifacts.list_prefix("chase") == []  # zero artifacts, always

        record = backend.get(result.run_id)
        assert record.status == "rejected"
        # Both values visible side by side in the run record.
        assert record.validator_verdict["evidence"]["calculator_date"] == "2026-01-29"
        assert record.validator_verdict["evidence"]["model_claimed_date"] == "2026-02-15"


class TestAgreementIssuesPacket:
    def test_agreement_produces_a_packet(self):
        backend = MemoryBackend()
        artifacts = LocalBackend()
        chase = _sample_chase(model_claimed_date=date(2026, 1, 29))  # correct

        result = run_chase_tick(chase, backend, artifacts)

        assert result.status == "complete"
        assert result.artifact_uri is not None

        paths = artifacts.list_prefix("chase")
        assert any(p.endswith("packet.pdf") for p in paths)
        assert any(p.endswith("log.jsonl") for p in paths)

        pdf_bytes = artifacts.read(f"chase/{result.run_id}/packet.pdf")
        assert pdf_bytes.startswith(b"%PDF")

        log_lines = artifacts.read(f"chase/{result.run_id}/log.jsonl").decode().strip().splitlines()
        assert len(log_lines) == 1
        first = json.loads(log_lines[0])
        assert first["event"] == "packet_issued"
        assert first["next_eligible_date"] == "2026-01-29"

    def test_no_model_claim_still_issues_packet_from_calculator_alone(self):
        backend = MemoryBackend()
        artifacts = LocalBackend()
        chase = _sample_chase(model_claimed_date=None)

        result = run_chase_tick(chase, backend, artifacts)
        assert result.status == "complete"


class TestIdempotency:
    def test_duplicate_tick_writes_exactly_one_packet(self):
        backend = MemoryBackend()
        artifacts = LocalBackend()
        chase = _sample_chase(model_claimed_date=date(2026, 1, 29))

        first = run_chase_tick(chase, backend, artifacts)
        second = run_chase_tick(chase, backend, artifacts)

        assert first.run_id == second.run_id
        assert second.status == "skipped_complete"

        packet_paths = [p for p in artifacts.list_prefix("chase") if p.endswith("packet.pdf")]
        assert len(packet_paths) == 1


class TestFollowupTick:
    def test_followup_appends_delayed_second_entry(self):
        backend = MemoryBackend()
        artifacts = LocalBackend()
        chase = _sample_chase(model_claimed_date=date(2026, 1, 29))
        result = run_chase_tick(chase, backend, artifacts)
        assert result.status == "complete"

        log_path = f"chase/{result.run_id}/log.jsonl"
        before = artifacts.read(log_path).decode().strip().splitlines()
        assert len(before) == 1

        later = datetime(2026, 1, 20, tzinfo=timezone.utc)  # genuinely later
        written = append_followup_entry(
            result.run_id, backend, artifacts,
            followup_text="Following up: still awaiting payer callback.",
            now=later,
        )
        assert written is not None

        after = artifacts.read(log_path).decode().strip().splitlines()
        assert len(after) == 2
        second_entry = json.loads(after[1])
        assert second_entry["event"] == "followup_drafted"
        assert second_entry["ts"] == later.isoformat()

    def test_followup_is_idempotent(self):
        backend = MemoryBackend()
        artifacts = LocalBackend()
        chase = _sample_chase(model_claimed_date=date(2026, 1, 29))
        result = run_chase_tick(chase, backend, artifacts)

        append_followup_entry(result.run_id, backend, artifacts, "first pass")
        second_call = append_followup_entry(result.run_id, backend, artifacts, "second pass")
        assert second_call is None  # already followed up, no-op

        log_path = f"chase/{result.run_id}/log.jsonl"
        lines = artifacts.read(log_path).decode().strip().splitlines()
        assert len(lines) == 2  # not 3

    def test_followup_no_op_on_rejected_run(self):
        backend = MemoryBackend()
        artifacts = LocalBackend()
        chase = _sample_chase(model_claimed_date=date(2026, 2, 15))  # wrong -> rejected
        result = run_chase_tick(chase, backend, artifacts)
        assert result.status == "rejected"

        written = append_followup_entry(result.run_id, backend, artifacts, "n/a")
        assert written is None
