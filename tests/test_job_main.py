"""Tests for job/main.py's chase path: the ADK agent must actually be
invoked, not just importable.

Mirrors projects/sovereign/tests/test_job_tick.py's "the fleet must be on
the product path, not a test fixture" section. Previously job/main.py's
chase mode read REFILL_MODEL_CLAIMED_DATE straight into the validator and
never touched agent/refill_agent.py's LlmAgent at all -- the deployed
Cloud Run Job only ever exercised the calculator, never the dialogue
agent (see progress/JUDGE_PANEL_POST_DEPLOY.md's Refill section). These
tests fail if that wiring is ever removed again.

No live Gemini call is made here: `run_single_turn` is monkeypatched to a
fake coroutine that drives the SAME `propose_next_eligible_date` tool
closure `agent/refill_agent.py` builds (via `build_refill_agent`), so the
calculator-adjudication logic under test is real -- only the model's
text-generation call is stubbed out, exactly the way
projects/sovereign/tests/test_fleet.py exercises tool closures directly
instead of making a live model call.
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest

from agentspine import LocalBackend, MemoryBackend

from agent.refill_agent import ProposalRecord, build_refill_agent
from job import main as job_main


# --- The ADK agent must be on the product path, not just importable -------


def test_job_main_chase_invokes_the_real_adk_agent():
    """job/main.py's main() must actually run the ADK agent in chase mode.
    Fails if _run_dialogue_agent (or an equivalent real agent invocation)
    is ever removed from the chase branch again."""
    src = inspect.getsource(job_main.main)
    assert "_run_dialogue_agent(" in src, (
        "job/main.py's chase path no longer calls the ADK dialogue agent; "
        "it would be reading REFILL_MODEL_CLAIMED_DATE straight into the "
        "validator again with zero model involvement"
    )


def test_run_dialogue_agent_requires_an_api_key(monkeypatch):
    """No silent offline fallback: with no GOOGLE_API_KEY/GEMINI_API_KEY,
    the chase path must fail loudly rather than fabricate a model
    response, matching agent/run_chat.py's own guarantee."""
    for var in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    from job.tick import ChaseInput

    sample = ChaseInput(
        user_id="caregiver-alice",
        medication="Enbrel (etanercept) 50mg/mL",
        ndc="58406-0435-1",
        plan="standard",
        last_fill_date=date(2026, 1, 1),
        days_supply=30,
        denial_reason="Refill too soon.",
        model_claimed_date=None,
        window_start="2026-01-15",
    )

    with pytest.raises(SystemExit) as excinfo:
        job_main._run_dialogue_agent(sample, None)
    assert "GOOGLE_API_KEY" in str(excinfo.value)


def test_run_dialogue_agent_calls_the_same_propose_tool_and_returns_its_date(
    monkeypatch,
):
    """Stub only the model call (run_single_turn); the tool closure that
    actually runs is agent/refill_agent.py's real
    propose_next_eligible_date, unmodified. Proves _run_dialogue_agent's
    returned model_claimed_date really came from that tool's log, not
    from REFILL_MODEL_CLAIMED_DATE or any other shortcut."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used-for-a-call")

    from job.tick import ChaseInput

    chase = ChaseInput(
        user_id="caregiver-alice",
        medication="Enbrel (etanercept) 50mg/mL",
        ndc="58406-0435-1",
        plan="standard",
        last_fill_date=date(2026, 1, 1),
        days_supply=30,
        denial_reason="Refill too soon.",
        model_claimed_date=None,
        window_start="2026-01-15",
    )

    async def fake_run_single_turn(prompt, user_id, proposal_log):
        # Drive the REAL propose_next_eligible_date tool closure, the same
        # one build_refill_agent binds into the LlmAgent -- only the
        # model's own text generation is faked here.
        agent = build_refill_agent(proposal_log)
        tool = agent.tools[0]
        result = tool.func(
            last_fill_date=chase.last_fill_date.isoformat(),
            days_supply=chase.days_supply,
            plan=chase.plan,
            model_claimed_date="2026-01-29",  # correct guess
        )
        return f"proposed: {result}"

    monkeypatch.setattr(job_main, "run_single_turn", fake_run_single_turn)

    claimed, proposal_log, reply = job_main._run_dialogue_agent(chase, None)

    assert claimed == date(2026, 1, 29)
    assert len(proposal_log) == 1
    assert isinstance(proposal_log[0], ProposalRecord)
    assert proposal_log[0].agreed is True
    assert "AGREE" in reply


def test_job_main_chase_disagreement_still_blocks_the_packet(monkeypatch, tmp_path):
    """End-to-end through job/main.py's real main(): the model's proposal
    (via the stubbed run_single_turn, real tool closure) disagreeing with
    the calculator must still reject the run and write zero artifacts --
    the calculator's veto survives wiring the agent onto this path."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used-for-a-call")
    monkeypatch.setenv("REFILL_LAST_FILL", "2026-01-01")
    monkeypatch.setenv("REFILL_DAYS_SUPPLY", "30")
    monkeypatch.setenv("REFILL_PLAN", "standard")
    monkeypatch.setenv("REFILL_LOCAL_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.delenv("REFILL_USE_FIRESTORE", raising=False)
    monkeypatch.delenv("REFILL_BUCKET", raising=False)
    monkeypatch.delenv("REFILL_MODEL_CLAIMED_DATE", raising=False)
    monkeypatch.setenv("REFILL_WINDOW", "test-window-disagree")

    async def fake_run_single_turn(prompt, user_id, proposal_log):
        agent = build_refill_agent(proposal_log)
        tool = agent.tools[0]
        result = tool.func(
            last_fill_date="2026-01-01",
            days_supply=30,
            plan="standard",
            model_claimed_date="2026-02-15",  # wrong guess
        )
        return f"proposed: {result}"

    monkeypatch.setattr(job_main, "run_single_turn", fake_run_single_turn)

    exit_code = job_main.main()

    assert exit_code == 0
    artifacts = LocalBackend(str(tmp_path))
    assert artifacts.list_prefix("chase") == []  # zero artifacts, always


def test_job_main_chase_agreement_issues_the_packet(monkeypatch, tmp_path):
    """The mirror case: a model proposal that agrees with the calculator
    (again via the real tool closure) still lets the packet through."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used-for-a-call")
    monkeypatch.setenv("REFILL_LAST_FILL", "2026-01-01")
    monkeypatch.setenv("REFILL_DAYS_SUPPLY", "30")
    monkeypatch.setenv("REFILL_PLAN", "standard")
    monkeypatch.setenv("REFILL_LOCAL_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.delenv("REFILL_USE_FIRESTORE", raising=False)
    monkeypatch.delenv("REFILL_BUCKET", raising=False)
    monkeypatch.delenv("REFILL_MODEL_CLAIMED_DATE", raising=False)
    monkeypatch.setenv("REFILL_WINDOW", "test-window-agree")

    async def fake_run_single_turn(prompt, user_id, proposal_log):
        agent = build_refill_agent(proposal_log)
        tool = agent.tools[0]
        result = tool.func(
            last_fill_date="2026-01-01",
            days_supply=30,
            plan="standard",
            model_claimed_date="2026-01-29",  # correct guess
        )
        return f"proposed: {result}"

    monkeypatch.setattr(job_main, "run_single_turn", fake_run_single_turn)

    exit_code = job_main.main()

    assert exit_code == 0
    artifacts = LocalBackend(str(tmp_path))
    paths = artifacts.list_prefix("chase")
    assert any(p.endswith("packet.pdf") for p in paths)
