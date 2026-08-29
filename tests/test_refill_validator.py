"""Tests for validator/refill_validator.py -- the veto itself.

This is the file the entire hackathon thesis rests on: if the model's
claimed date disagrees with the calculator, PASSED must be False, with
both dates visible in evidence. If it agrees, PASSED must be True.
"""

from __future__ import annotations

from datetime import date

from validator.refill_validator import EligibilityValidator


def test_agreement_passes():
    v = EligibilityValidator()
    verdict = v.verdict({
        "last_fill_date": date(2026, 1, 1),
        "days_supply": 30,
        "plan": "standard",
        "model_claimed_date": date(2026, 1, 29),  # correct: 30 - 2 early days
    })
    assert verdict.passed is True
    assert verdict.evidence["calculator_date"] == "2026-01-29"


def test_disagreement_fails_with_both_dates_in_evidence():
    v = EligibilityValidator()
    verdict = v.verdict({
        "last_fill_date": date(2026, 1, 1),
        "days_supply": 30,
        "plan": "standard",
        "model_claimed_date": date(2026, 2, 1),  # wrong: model guessed +31 days
    })
    assert verdict.passed is False
    assert verdict.evidence["calculator_date"] == "2026-01-29"
    assert verdict.evidence["model_claimed_date"] == "2026-02-01"
    assert "disagrees" in verdict.reason


def test_no_model_claim_yet_still_passes_with_calculator_date():
    v = EligibilityValidator()
    verdict = v.verdict({
        "last_fill_date": date(2026, 1, 1),
        "days_supply": 30,
        "plan": "default",
        "model_claimed_date": None,
    })
    assert verdict.passed is True
    assert verdict.evidence["calculator_date"] == "2026-01-31"


def test_missing_days_supply_fails_closed():
    v = EligibilityValidator()
    verdict = v.verdict({
        "last_fill_date": date(2026, 1, 1),
        "days_supply": None,
        "plan": "standard",
        "model_claimed_date": date(2026, 1, 29),
    })
    assert verdict.passed is False
    assert "calculator could not run" in verdict.reason


def test_plan_quirk_disagreement_is_caught():
    # Model assumes "default" (0 early days) but the real plan is
    # "generous" (7 early days) -- a classic caregiver mistake this
    # validator exists to catch.
    v = EligibilityValidator()
    verdict = v.verdict({
        "last_fill_date": date(2026, 1, 1),
        "days_supply": 30,
        "plan": "generous",
        "model_claimed_date": date(2026, 1, 31),  # would be right for "default"
    })
    assert verdict.passed is False
    assert verdict.evidence["calculator_date"] == "2026-01-24"
