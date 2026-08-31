# Refill

**Try it out (no install): https://refill-collaborative-agent.vercel.app** runs
the offline end-to-end demo in your browser and shows the wrong date getting
vetoed live.

An adult child is managing a parent's specialty medication refill denial.
Refill asks the questions a pharmacy would ask, computes eligibility
itself, and produces a one-page packet and phone script before the
caregiver calls the payer.

**Track: The Collaborative Partner.**

Contest requirements, and where each one lives in this repo:

| Requirement | What this project uses | Where |
|---|---|---|
| Gemini 2.5 Flash or newer | `gemini-2.5-flash` | `agent/refill_agent.py:30` |
| Google agent framework | Agent Development Kit (`google-adk`), `LlmAgent` with the calculator bound as a tool | `agent/refill_agent.py` |
| Google Cloud service | Cloud Run Jobs, Cloud Scheduler, Firestore (profile memory), GCS (packet artifacts) | `infra/deploy.sh` |

## Quick start

Verified from a clean `git clone` into an empty directory, with a fresh
virtualenv and no other setup.

**Python 3.11 or newer is required.** Check first, because the default
`python3` on macOS is often 3.9, and an old `pip` fails the editable
install below with a confusing "requires a setuptools-based build" error
rather than a version error:

```bash
python3 --version        # must be 3.11 or newer; use python3.12 explicitly if not
```

```bash
git clone <this-repo> refill
cd refill
python3.12 -m venv .venv          # or: python3 -m venv .venv, if python3 is >= 3.11
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
PY=.venv/bin/python make test
PY=.venv/bin/python make demo
```

`pip install -e .` installs every dependency, including the `agentspine`
spine that ships inside this repo. There is no sibling directory to clone
and no `PYTHONPATH` to export.

Expected output: **73 passing tests**, and a demo that exits 0. Both run
fully offline, with no network calls, no GCP credentials, and no API key.
If either needs one, that is a bug in this project. (`make job`, below,
is the exception: it runs the real Cloud Run Job entrypoint, including a
live Gemini call, and does require an API key.)

## The validator (why this isn't a wrapper)

`validator/eligibility.py` is pure arithmetic:

```
next_eligible = last_fill_date + days_supply - allowed_early_days(plan)
```

Zero model involvement. Gemini proposes a next-eligible date through the
`propose_next_eligible_date` ADK tool (`agent/refill_agent.py`); the tool
runs the calculator itself and compares. If they disagree, the calculator
wins: `agentspine.run_tick` never calls the packet writer on a failed
verdict, so **zero packets are produced for a disagreement**. Both dates
are written to the run record side by side.

**Delete-the-validator test:** see `RED_GREEN.md` for the recorded proof
that bypassing the veto lets a wrong date reach a real `packet.pdf`, and
that restoring it blocks the run again.

## What the commands do

```bash
make test
```

The full offline suite (73 tests) across the eligibility calculator, the
discrepancy validator, the denial letter parser, the dialogue state
machine, the ADK agent wiring, `job/main.py`'s chase path invoking that
ADK agent (with the model call stubbed and the real tool closure driven
directly, so the calculator-adjudication logic under test is real),
Firestore-backed profile memory including deletion and correction, the
packet writer, and the idempotent job tick.

```bash
make demo
```

Runs `demo_local.py`: a scripted transcript with zero network calls, zero
GCP credentials, and a fake model response in place of Gemini. It walks
the blocked case (the model's date disagrees with the calculator), the
issued case (`packet.pdf` written), the idempotency claim, the delayed
follow-up tick, and a second chase session that skips already-known
fields, then a `forget_fact` that makes the question come back. Each
invariant is asserted as it goes, and the demo exits non-zero if any step
does not hold. `demo_local.py` exercises `job/tick.py` directly (a fake
model response, matching its own claim above); it does not go through
`job/main.py`.

```bash
make job
```

Runs `job/main.py`, the same Cloud Run Job entrypoint the cloud deploy
invokes, against the offline Memory and Local backends. **Chase mode
requires `GOOGLE_API_KEY` (or `GEMINI_API_KEY`)**: it now calls the real
ADK `LlmAgent` (`agent/refill_agent.py`) for one turn, through the same
`InMemoryRunner` wiring `agent/run_chat.py`'s manual CLI uses, and there
is no offline fallback on this path -- with no key set it raises loudly
and exits non-zero rather than fabricating a conversation. `REFILL_MODE=followup`
makes no model call and needs no key.

## Deploying to Google Cloud

```bash
make deploy PROJECT_ID=your-project-id
make teardown PROJECT_ID=your-project-id
```

`infra/deploy.sh` builds one image and deploys two Cloud Run Jobs from it:
`refill-chase` (validate and issue the packet, triggered when the
caregiver finishes the dialogue, which is a human act) and
`refill-followup` (the later tick that appends the genuinely time-delayed
second log entry), plus a Cloud Scheduler job pointed at the follow-up
only. Scheduling the chase itself would be background-execution theatre.

The deploy expects two service accounts, `refill-job-sa` and
`refill-scheduler-sa`, to already exist in the project.

**Honest status:** the deploy and teardown scripts are written and syntax
checked, but they have not been run end to end against a live billed GCP
project. Everything under "Quick start" has been verified from a clean
clone. See `LIMITATIONS.md`.

## Layout

- `validator/eligibility.py`: the calculator. Unit tested, including leap
  year, zero early days, and missing `days_supply`.
- `validator/refill_validator.py`: wraps the calculator as an
  `agentspine.Validator` (the veto).
- `agent/denial_letter.py`: deterministic field extraction from an
  uploaded denial letter.
- `agent/dialogue.py`: deterministic clarifying-question state machine:
  which fields are still missing, in what order.
- `agent/refill_agent.py`: the ADK `LlmAgent` (Gemini 2.5 Flash) with the
  calculator bound in as a tool.
- `agent/run_chat.py`: runs that `LlmAgent` against the live Gemini API
  via an `InMemoryRunner`. `run_single_turn` is the shared entrypoint both
  this module's own interactive CLI and `job/main.py`'s chase path call.
- `memory/profile.py`: Firestore-backed profile memory keyed by user and
  plan, with explicit `forget_fact` and `correct_fact` paths.
- `artifacts/packet.py`: the one-page reportlab PDF.
- `job/tick.py`: wires the validator and packet writer through
  `agentspine.run_tick`, plus the delayed follow-up tick that appends a
  second `log.jsonl` entry.
- `job/main.py`: the Cloud Run Job entrypoint. `REFILL_MODE` selects
  chase or followup. Chase mode calls the real ADK agent
  (`agent/run_chat.py::run_single_turn`) for its date proposal; the
  calculator still adjudicates it via `job/tick.py`.
- `agentspine/`: the shared spine this repo runs on.
- `fixtures/sample_denial_letter.txt`: synthetic sample, clearly marked,
  no real patient data.
- `tests/`: 73 offline tests, no network and no model calls (the ADK
  agent's own text-generation call is stubbed in `tests/test_job_main.py`;
  the tool closure it calls is real, unstubbed code).

## Bounded authority

Refill never submits anything to a payer or a pharmacy. It prepares the
human to make the call. The blast radius is a PDF.

## Judging access

This repository will be shared with `testing@devpost.com` and
`cloudhackathons@google.com` so the judges can clone it and run everything
above themselves.
