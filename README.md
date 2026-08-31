# Refill

Adult child managing a parent's specialty medication refill denial. Refill asks
the questions a pharmacy would ask, computes eligibility itself, and produces
a one-page packet and phone script before the caregiver calls the payer.

Track: Collaborative Partner.

**Status note:** the eligibility calculator, discrepancy validator, denial
letter parser, dialogue state machine, ADK agent, Firestore-backed profile
memory (with deletion), packet PDF writer, and the idempotent job tick are
all implemented and pass 64 local tests. `infra/` (the `gcloud` deploy
scripts and Cloud Scheduler wiring) is not written yet; see
`LIMITATIONS.md`.

## The validator (why this isn't a wrapper)

`validator/eligibility.py` is pure arithmetic:

```
next_eligible = last_fill_date + days_supply - allowed_early_days(plan)
```

Zero model involvement. Gemini proposes a next-eligible date through the
`propose_next_eligible_date` ADK tool (`agent/refill_agent.py`); the tool
runs the calculator itself and compares. If they disagree, the calculator
wins: `agentspine.run_tick` never calls the packet writer on a failed
verdict, so **zero packets are produced for a disagreement**. Both dates are
written to the run record side by side.

**Delete-the-validator test:** see `RED_GREEN.md` for the recorded proof that
bypassing the veto lets a wrong date reach a real packet.pdf, and that
restoring it blocks the run again.

## Layout

- `validator/eligibility.py` — the calculator. Unit tested (leap year, zero
  early days, missing days_supply).
- `validator/refill_validator.py` — wraps the calculator as an
  `agentspine.Validator` (the veto).
- `agent/denial_letter.py` — deterministic field extraction from an uploaded
  denial letter.
- `agent/dialogue.py` — deterministic clarifying-question state machine
  (which fields are still missing, in what order).
- `agent/refill_agent.py` — the ADK `LlmAgent` (Gemini 3.5 Flash) with the
  calculator bound in as a tool.
- `memory/profile.py` — Firestore-backed profile memory keyed by user+plan,
  with explicit `forget_fact` / `correct_fact` deletion and correction paths.
- `artifacts/packet.py` — the one-page reportlab PDF.
- `job/tick.py` — wires the validator + packet writer through
  `agentspine.run_tick`, plus the delayed follow-up tick that appends a
  second `log.jsonl` entry.
- `fixtures/sample_denial_letter.txt` — synthetic sample, clearly marked, no
  real PHI.
- `tests/` — 64 offline pytest tests, no network or model calls.

## Run tests

```bash
cd projects/refill
../../.venv/bin/pip install -r requirements.txt   # once, into the shared repo-root venv
make test
```

Verified 66/66 passing.

## Run the demo

```bash
make demo
```

Runs `demo_local.py`: a scripted transcript with zero network calls, zero
GCP credentials, and a fake model response in place of Gemini. Walks the
blocked case (model date disagrees with the calculator), the issued case
(packet.pdf written), the idempotency claim, the delayed follow-up tick,
and a second chase session that skips already-known fields, then a
`forget_fact` that makes the question come back. Asserts each invariant
as it goes; exits non-zero if any step doesn't hold.

## Bounded authority

Refill never submits anything to a payer or pharmacy. It prepares the human
to make the call. Blast radius is a PDF.
