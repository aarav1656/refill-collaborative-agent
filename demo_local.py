#!/usr/bin/env python3
"""demo_local.py — the full Refill loop, offline, no GCP, no API key.

Runs the real production path: the denial-letter parser, the dialogue state
machine, profile memory, the deterministic eligibility calculator, and
`job.tick.run_chase_tick` (which goes through agentspine.run_tick). The
only fake is the model's *claimed date* -- instead of a live Gemini call we
inject the date a model asserted, which is exactly the field the validator
adjudicates. Everything the validator does with it is production code.

Acts:
    1. INTAKE: parse a synthetic denial letter, run the clarifying-question
       dialogue, compute eligibility.
    2. REJECT: the model claims a next-eligible date the calculator
       contradicts. Validator vetoes. Zero packets.
    3. ACCEPT: the model's claim matches the calculator. Packet + log
       written.
    4. IDEMPOTENCY: a second tick for the same (user, window) writes nothing.
    5. FOLLOW-UP: a later scheduled tick appends one follow-up entry, and
       only one.
    6. MEMORY: a second chase for the same user+plan asks fewer questions,
       and `forget` actually removes a fact.

Usage:  ../../.venv/bin/python demo_local.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentspine import transcript as t
from agentspine.artifacts import LocalBackend
from agentspine.idempotency import MemoryBackend, compute_run_id

from agent.denial_letter import parse_denial_letter
from agent.dialogue import DialogueSession
from agent.refill_agent import ProposalRecord, make_propose_tool
from job.tick import ChaseInput, append_followup_entry, run_chase_tick
from memory.profile import InMemoryMemoryService
from validator.eligibility import compute_next_eligible

USER = "caregiver-demo-1"
WINDOW = "2026-01-16"
LETTER = Path(__file__).resolve().parent / "fixtures" / "sample_denial_letter.txt"


def demo_artifact_root() -> str | None:
    """Where artifacts land.

    Default (None) is LocalBackend's own fresh temp dir, which is right for
    tests. For FILMING, demo/record_refill.sh sets DEMO_ARTIFACT_DIR to a
    fixed path so demo/watch_artifacts.py can be pointed at that exact
    directory in a second pane and be seen going from empty to non-empty on
    camera. A random temp dir cannot be watched, so the money shot needs this.
    """
    base = os.environ.get("DEMO_ARTIFACT_DIR")
    if not base:
        return None
    Path(base).mkdir(parents=True, exist_ok=True)
    return base


def chase_dirs(artifacts: LocalBackend) -> list[str]:
    return sorted({p.split("/")[1] for p in artifacts.list_prefix("chase") if "/" in p})


def main() -> int:
    t.header(
        "REFILL — offline end-to-end demo",
        "Collaborative Partner track. Zero network, zero GCP, zero API key.\n"
        "Validator: a deterministic eligibility calculator. If Gemini's claimed\n"
        "next-eligible date disagrees with the arithmetic, no packet is issued.",
    )

    idem = MemoryBackend()
    artifacts = LocalBackend(demo_artifact_root())
    memory = InMemoryMemoryService()
    t.note("idempotency backend: MemoryBackend (FirestoreBackend in prod)")
    t.note(f"artifact backend:    LocalBackend at {artifacts.root_dir} (GcsBackend in prod)")
    t.note("profile memory:      InMemoryMemoryService (FirestoreMemoryService in prod)")

    # ---------------------------------------------------------------- ACT 1
    t.act(1, "INTAKE — parse the denial letter, then ask only what's missing")
    parsed = parse_denial_letter(LETTER.read_text())
    t.step("parsed the synthetic denial letter (no PHI, fabricated fixture)")
    t.fact("medication", parsed.medication)
    t.fact("ndc", parsed.ndc)
    t.fact("plan", parsed.plan)
    t.fact("last_fill_date", parsed.last_fill_date)
    t.fact("days_supply", parsed.days_supply)
    t.fact("denial_reason", (parsed.denial_reason or "")[:56] + "...")

    session = DialogueSession(user_id=USER)
    session.prefill_from_letter(parsed)
    session.prefill_from_memory(memory.get_profile(USER, parsed.plan))
    asked_first_run = []
    while (field := session.next_question_field()) is not None:
        asked_first_run.append(field)
        session.mark_asked(field)
        if field == "prior_attempts":
            session.answer(field, "called the payer once, no callback")
    t.step("clarifying questions the agent still needed to ask")
    t.fact("questions asked", asked_first_run or "none (letter had everything)")
    t.fact("ready for eligibility", session.is_ready_for_eligibility())

    calc = compute_next_eligible(parsed.last_fill_date, parsed.days_supply, parsed.plan)
    t.step("the deterministic calculator (this is the veto, no model involved)")
    t.fact("last_fill + days_supply", f"{parsed.last_fill_date} + {parsed.days_supply}d")
    t.fact("plan early-refill grace", f"{calc.early_days}d ({calc.plan})")
    t.fact("CALCULATOR next eligible", calc.next_eligible)

    t.assert_demo(session.is_ready_for_eligibility(), "dialogue must reach eligibility")

    # ---------------------------------------------------------------- ACT 2
    t.act(2, "REJECT — the model asserts a date the arithmetic contradicts")
    wrong_date = calc.next_eligible - timedelta(days=5)
    t.step("Gemini's claimed next-eligible date (the fake model output)")
    t.fact("MODEL claims", wrong_date)
    t.fact("CALCULATOR says", calc.next_eligible)
    t.fact("disagreement", f"{(calc.next_eligible - wrong_date).days} days")

    t.step("the same claim through the ADK tool the live agent calls")
    proposal_log: list[ProposalRecord] = []
    tool = make_propose_tool(proposal_log)
    tool_reply = tool.func(
        last_fill_date=parsed.last_fill_date.isoformat(),
        days_supply=parsed.days_supply,
        plan=parsed.plan,
        model_claimed_date=wrong_date.isoformat(),
    )
    t.fact("tool response", tool_reply[:64] + "...")
    t.note("the model is instructed to relay this verbatim and may not retry")
    t.note("with a different guess -- the calculator's arithmetic is final.")

    bad_chase = ChaseInput(
        user_id=USER,
        medication=parsed.medication,
        ndc=parsed.ndc,
        plan=parsed.plan,
        last_fill_date=parsed.last_fill_date,
        days_supply=parsed.days_supply,
        denial_reason=parsed.denial_reason,
        model_claimed_date=wrong_date,
        window_start=WINDOW,
    )
    rejected = run_chase_tick(bad_chase, idem, artifacts)
    # Rendered from the REAL verdict, never a hardcoded False. If the
    # eligibility calculator is bypassed (demo/break_validator.sh), this
    # line must visibly flip to PASSED on camera.
    t.verdict_line(
        bool(rejected.verdict and rejected.verdict.passed),
        rejected.verdict.reason if rejected.verdict else "",
    )
    t.fact("tick status", rejected.status)
    t.artifacts(
        [f"{artifacts.root_dir}/{p}" for p in sorted(artifacts.list_prefix("chase"))],
        empty_note="no packet.pdf was produced",
    )

    t.assert_demo(rejected.status == "rejected", "disagreement must be rejected")
    t.assert_demo(chase_dirs(artifacts) == [], "rejected run writes zero packets")
    t.assert_demo(proposal_log[-1].agreed is False, "tool must record disagreement")

    # ---------------------------------------------------------------- ACT 3
    t.act(3, "ACCEPT — the model's claim matches the calculator")
    good_chase = ChaseInput(**{**bad_chase.__dict__, "model_claimed_date": calc.next_eligible})
    t.fact("MODEL claims", calc.next_eligible)
    t.fact("CALCULATOR says", calc.next_eligible)

    accepted = run_chase_tick(good_chase, idem, artifacts)
    t.verdict_line(True, accepted.verdict.reason if accepted.verdict else "")
    t.fact("tick status", accepted.status)
    t.fact("run_id", accepted.run_id[:16] + "...")
    written = sorted(artifacts.list_prefix("chase"))
    t.artifacts([f"{artifacts.root_dir}/{p}" for p in written])

    pdf = artifacts.read(f"chase/{accepted.run_id}/packet.pdf")
    t.fact("packet.pdf bytes", len(pdf))
    t.fact("is a real PDF", pdf[:5] == b"%PDF-")
    t.note("the packet's next-eligible date is the CALCULATOR's date by")
    t.note("construction: artifact_fn reads it out of verdict.evidence, and")
    t.note("run_tick only calls artifact_fn on a passing verdict.")
    t.note("Refill drafts a packet and a phone script. It never submits to a payer.")

    t.assert_demo(accepted.status == "complete", "agreement must complete")
    t.assert_demo(len(chase_dirs(artifacts)) == 1, "exactly one chase folder")
    t.assert_demo(pdf[:5] == b"%PDF-", "packet must be a real PDF")

    # ---------------------------------------------------------------- ACT 4
    t.act(4, "IDEMPOTENCY — the Scheduler fires again for the same chase")
    t.fact("deterministic run_id", compute_run_id(USER, WINDOW)[:16] + "...")
    t.fact("matches act 3", compute_run_id(USER, WINDOW) == accepted.run_id)
    second = run_chase_tick(good_chase, idem, artifacts)
    t.fact("tick status", second.status)
    t.fact("chase folders now", len(chase_dirs(artifacts)))
    t.assert_demo(second.status == "skipped_complete", "duplicate tick must skip")
    t.assert_demo(len(chase_dirs(artifacts)) == 1, "still exactly one chase folder")

    # ---------------------------------------------------------------- ACT 5
    t.act(5, "FOLLOW-UP — work that outlives the HTTP request")
    log_path = f"chase/{accepted.run_id}/log.jsonl"
    t.fact("log entries before", len(artifacts.read(log_path).decode().strip().splitlines()))
    later = datetime.now(timezone.utc) + timedelta(days=3)
    append_followup_entry(
        accepted.run_id, idem, artifacts,
        "No payer response after 3 days. Draft: call the plan's PA line and "
        "reference the calculator's next-eligible date.",
        now=later,
    )
    entries = artifacts.read(log_path).decode().strip().splitlines()
    t.fact("log entries after", len(entries))
    for e in entries:
        t.fact("  event", json.loads(e)["event"])
    t.step("the same follow-up tick again (idempotent)")
    append_followup_entry(accepted.run_id, idem, artifacts, "duplicate", now=later)
    entries_again = artifacts.read(log_path).decode().strip().splitlines()
    t.fact("log entries", len(entries_again))

    t.assert_demo(len(entries) == 2, "follow-up appends exactly one entry")
    t.assert_demo(len(entries_again) == 2, "a repeat follow-up appends nothing")

    # ---------------------------------------------------------------- ACT 6
    t.act(6, "MEMORY — the second chase is shorter, and 'forget' really forgets")
    memory.remember_fact(USER, parsed.plan, "prior_attempts",
                         "called the payer once, no callback")
    memory.remember_fact(USER, parsed.plan, "plan_quirk",
                         "this payer wants days_supply spelled out on every call")
    t.fact("facts remembered", sorted(memory.get_profile(USER, parsed.plan).active_facts()))

    second_session = DialogueSession(user_id=USER)
    second_session.prefill_from_letter(parsed)
    second_session.prefill_from_memory(memory.get_profile(USER, parsed.plan))
    asked_second_run = []
    while (field := second_session.next_question_field()) is not None:
        asked_second_run.append(field)
        second_session.mark_asked(field)
    t.fact("questions run 1", len(asked_first_run))
    t.fact("questions run 2", len(asked_second_run))
    t.note("fewer questions is a measured fact, not a vibe: the dialogue state")
    t.note("machine skips any field profile memory already knows.")

    t.step("caregiver says 'forget that'")
    forgot = memory.forget_fact(USER, parsed.plan, "prior_attempts")
    t.fact("forget_fact returned", forgot)
    t.fact("facts now active", sorted(memory.get_profile(USER, parsed.plan).active_facts()))
    t.fact("second forget (no-op)", memory.forget_fact(USER, parsed.plan, "prior_attempts"))

    t.assert_demo(len(asked_second_run) < len(asked_first_run),
                  "the remembered run must ask strictly fewer questions")
    t.assert_demo(forgot is True, "forget must actually delete")
    t.assert_demo("prior_attempts" not in memory.get_profile(USER, parsed.plan).active_facts(),
                  "a forgotten fact must not come back")

    # ------------------------------------------------------------- SUMMARY
    t.summary([
        ("model date disagrees with calculator", "REJECTED, 0 packets"),
        ("model date matches calculator", "ACCEPTED, packet.pdf written"),
        ("duplicate scheduler tick", "skipped_complete, still 1 packet"),
        ("follow-up tick", "1 appended entry, repeat is a no-op"),
        ("second chase with memory", f"{len(asked_first_run)} -> {len(asked_second_run)} questions"),
        ("forget_fact", "fact gone from active retrievals"),
        ("network calls", "0"),
        ("GCP credentials required", "none"),
    ])
    print("\nDelete the eligibility calculator and act 2 turns into act 3:")
    print("the wrong date gets a packet printed on it. That is the whole project.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
