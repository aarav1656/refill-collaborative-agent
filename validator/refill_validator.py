"""Validator: wraps the eligibility calculator as an agentspine.Validator.

This is the veto. `verdict()` is pure arithmetic comparison, no model call.
The model's claimed date is just one field in the context dict; if it
disagrees with the calculator, PASSED=False and the run is rejected
(agentspine.run_tick never calls artifact_fn on a failed verdict, so zero
packets are produced for a disagreement).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from agentspine import Verdict

from validator.eligibility import EligibilityError, compute_next_eligible


@dataclass
class EligibilityValidator:
    """agentspine.Validator implementation for Refill.

    context must contain:
        last_fill_date: date
        days_supply: int
        plan: str
        model_claimed_date: Optional[date]  -- what Gemini asserted, or None
            if the model made no date claim this turn.

    If model_claimed_date is None, the validator still computes the
    calculator's date and PASSES (there's nothing to disagree with yet;
    e.g. mid-dialogue before the model has proposed anything). If
    model_claimed_date is present and differs from the calculator, the
    verdict is a hard fail with both dates in evidence.
    """

    def verdict(self, context: dict) -> "Verdict":
        last_fill_date = context.get("last_fill_date")
        days_supply = context.get("days_supply")
        plan = context.get("plan")
        model_claimed_date: Optional[date] = context.get("model_claimed_date")

        try:
            result = compute_next_eligible(last_fill_date, days_supply, plan)
        except EligibilityError as exc:
            return Verdict(
                passed=False,
                reason=f"calculator could not run: {exc}",
                evidence={"error": str(exc)},
            )

        calculator_date = result.next_eligible

        if model_claimed_date is None:
            return Verdict(
                passed=True,
                reason="calculator computed date, model made no competing claim",
                evidence={"calculator_date": calculator_date.isoformat()},
            )

        if model_claimed_date != calculator_date:
            return Verdict(
                passed=False,
                reason=(
                    "model-claimed next-eligible date disagrees with the "
                    "deterministic calculator"
                ),
                evidence={
                    "calculator_date": calculator_date.isoformat(),
                    "model_claimed_date": model_claimed_date.isoformat(),
                    "last_fill_date": result.last_fill_date.isoformat(),
                    "days_supply": result.days_supply,
                    "plan": result.plan,
                    "allowed_early_days": result.early_days,
                },
            )

        return Verdict(
            passed=True,
            reason="model-claimed date matches calculator",
            evidence={"calculator_date": calculator_date.isoformat()},
        )
