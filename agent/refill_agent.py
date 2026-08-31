"""Refill ADK agent: Gemini 3.5 Flash, multi-turn, with the calculator tool
bound in as the veto.

The LlmAgent asks clarifying questions (via ordinary dialogue) and, once it
believes it has enough information, proposes a next-eligible date by
calling the `propose_next_eligible_date` tool. That tool does NOT trust the
model's arithmetic: it runs `validator.eligibility.compute_next_eligible`
itself and returns the calculator's answer plus whether it matches what the
model claimed. The model is instructed to relay the tool's verdict verbatim
-- it cannot argue past a disagreement, mirroring Sovereign's "relay the
denial, don't retry" pattern.

No live model call is made in the offline test suite; the tests exercise
the tool closure directly (same approach as
projects/sovereign/tests/test_fleet.py), which is exactly the code path
the LLM would invoke.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from validator.eligibility import EligibilityError, compute_next_eligible

MODEL = "gemini-2.5-flash"  # gemini-3.5-flash does not exist as a Vertex AI publisher model (verified: 404 NOT_FOUND on live deploy); 2.5-flash is the real, available Gemini Flash model


@dataclass
class ProposalRecord:
    """One call to propose_next_eligible_date, kept for the run record and
    for tests to inspect without re-parsing model output."""

    model_claimed_date: Optional[str]
    calculator_date: str
    agreed: bool
    last_fill_date: str
    days_supply: int
    plan: str


def make_propose_tool(proposal_log: list[ProposalRecord]):
    """Build the ADK FunctionTool the agent calls to check its proposed
    date against the calculator. Every call is logged so the run record
    can show the model's claim and the calculator's answer side by side,
    per spec 02's "write both values side by side into the run record".
    """

    def propose_next_eligible_date(
        last_fill_date: str,
        days_supply: int,
        plan: str,
        model_claimed_date: str,
    ) -> str:
        """Check a proposed next-eligible refill date against the
        deterministic calculator.

        Args:
            last_fill_date: ISO date (YYYY-MM-DD) of the last pharmacy fill.
            days_supply: Number of days the last fill was meant to cover.
            plan: The payer plan name (e.g. "standard", "generous", "default").
            model_claimed_date: The ISO date (YYYY-MM-DD) you believe is the
                next-eligible refill date, computed from the letter and the
                conversation so far.

        Returns:
            A string reporting whether the calculator AGREES or DISAGREES
            with model_claimed_date. On DISAGREE, relay the calculator's
            date verbatim to the user -- do not argue past it or propose a
            different date; the calculator's arithmetic is final.
        """
        try:
            last_fill = date.fromisoformat(last_fill_date)
        except ValueError:
            return f"ERROR: last_fill_date '{last_fill_date}' is not a valid ISO date"

        try:
            result = compute_next_eligible(last_fill, days_supply, plan)
        except EligibilityError as exc:
            return f"ERROR: calculator could not run: {exc}"

        calc_date = result.next_eligible
        try:
            claimed = date.fromisoformat(model_claimed_date)
        except ValueError:
            claimed = None

        agreed = claimed == calc_date
        proposal_log.append(ProposalRecord(
            model_claimed_date=model_claimed_date,
            calculator_date=calc_date.isoformat(),
            agreed=agreed,
            last_fill_date=last_fill.isoformat(),
            days_supply=days_supply,
            plan=result.plan,
        ))

        if agreed:
            return f"AGREE: calculator confirms next-eligible date is {calc_date.isoformat()}"
        return (
            f"DISAGREE: calculator computed {calc_date.isoformat()}, "
            f"which differs from your claimed {model_claimed_date}. "
            "The calculator's date is authoritative. Relay this date to "
            "the user; do not restate your original claim."
        )

    propose_next_eligible_date.__name__ = "propose_next_eligible_date"
    return FunctionTool(propose_next_eligible_date)


def build_refill_agent(proposal_log: list[ProposalRecord]) -> LlmAgent:
    """Assemble the Refill LlmAgent with the calculator tool bound in."""

    return LlmAgent(
        name="refill_agent",
        model=MODEL,
        description=(
            "Helps a caregiver chase a specialty-medication prior "
            "authorization refill denial. Asks clarifying questions "
            "about plan, days supply, and prior attempts, then proposes "
            "a next-eligible refill date."
        ),
        instruction=(
            "You help an adult child managing a parent's specialty "
            "medication refill denial. You are not a clinician. Ask "
            "clarifying questions one at a time: which plan, how many "
            "days the last fill was supposed to cover, and whether they "
            "have already tried calling the payer. Once you have a "
            "last_fill_date, days_supply, and plan, call "
            "propose_next_eligible_date with your best-guess "
            "model_claimed_date. If the tool returns DISAGREE, you MUST "
            "relay the calculator's date verbatim and MUST NOT argue for "
            "your original date or try again with a different guess -- "
            "the calculator's arithmetic is authoritative and final. "
            "Never tell the caregiver to submit anything to the payer "
            "yourself; you only prepare them to make the call."
        ),
        tools=[make_propose_tool(proposal_log)],
    )
