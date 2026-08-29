"""Eligibility calculator: the deterministic veto over the model.

Pure arithmetic. Zero model involvement. This is the thing that makes Refill
not a wrapper (AIM.md: "delete the validator, if the demo still works it's a
wrapper and it loses").

    next_eligible = last_fill_date + days_supply - allowed_early_days(plan)

If Gemini's narrative asserts a next-eligible date that disagrees with this
arithmetic, the calculator wins and the packet is not issued (spec 02).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


class EligibilityError(ValueError):
    """Raised when the inputs are insufficient to compute eligibility.

    Never guess. A missing days_supply is a hard stop, not a default.
    """


# Plan quirks: how many days early a payer allows a refill. Real payers vary
# this per plan; unknown plans default to 0 (safest: no early refill).
PLAN_EARLY_DAYS: dict[str, int] = {
    "default": 0,
    "standard": 2,
    "generous": 7,
}


def allowed_early_days(plan: Optional[str]) -> int:
    """Return the plan's allowed-early-refill window in days.

    Unknown or missing plan -> 0 (most conservative: no early refill grace).
    """
    if not plan:
        return PLAN_EARLY_DAYS["default"]
    return PLAN_EARLY_DAYS.get(plan.strip().lower(), PLAN_EARLY_DAYS["default"])


@dataclass(frozen=True)
class EligibilityResult:
    next_eligible: date
    last_fill_date: date
    days_supply: int
    early_days: int
    plan: str

    def to_dict(self) -> dict:
        return {
            "next_eligible": self.next_eligible.isoformat(),
            "last_fill_date": self.last_fill_date.isoformat(),
            "days_supply": self.days_supply,
            "early_days": self.early_days,
            "plan": self.plan,
        }


def compute_next_eligible(
    last_fill_date: date,
    days_supply: Optional[int],
    plan: Optional[str],
) -> EligibilityResult:
    """The single source of truth for "when can this refill happen".

    next_eligible = last_fill_date + days_supply - allowed_early_days(plan)

    Raises EligibilityError if days_supply is missing, non-positive, or
    last_fill_date is not a date. No silent defaults for days_supply: a
    caregiver acting on a wrong guessed number is exactly the failure mode
    this whole project exists to prevent.
    """
    if not isinstance(last_fill_date, date):
        raise EligibilityError(
            f"last_fill_date must be a date, got {type(last_fill_date).__name__}"
        )
    if days_supply is None:
        raise EligibilityError("days_supply is required and was not provided")
    if not isinstance(days_supply, int) or isinstance(days_supply, bool):
        raise EligibilityError(
            f"days_supply must be an int, got {type(days_supply).__name__}"
        )
    if days_supply <= 0:
        raise EligibilityError(f"days_supply must be positive, got {days_supply}")

    early = allowed_early_days(plan)
    next_eligible = last_fill_date + timedelta(days=days_supply - early)

    return EligibilityResult(
        next_eligible=next_eligible,
        last_fill_date=last_fill_date,
        days_supply=days_supply,
        early_days=early,
        plan=(plan or "default").strip().lower(),
    )
