"""Unit tests for validator/eligibility.py -- pure arithmetic, no model.

Covers the spec's required edge cases: leap year, plan with 0 early days,
missing days_supply.
"""

from __future__ import annotations

from datetime import date

import pytest

from validator.eligibility import (
    EligibilityError,
    allowed_early_days,
    compute_next_eligible,
)


class TestAllowedEarlyDays:
    def test_default_plan_zero_days(self):
        assert allowed_early_days("default") == 0

    def test_none_plan_falls_back_to_default(self):
        assert allowed_early_days(None) == 0

    def test_empty_string_plan_falls_back_to_default(self):
        assert allowed_early_days("") == 0

    def test_standard_plan(self):
        assert allowed_early_days("standard") == 2

    def test_generous_plan(self):
        assert allowed_early_days("generous") == 7

    def test_unknown_plan_falls_back_to_default(self):
        assert allowed_early_days("some-payer-nobody-heard-of") == 0

    def test_case_insensitive(self):
        assert allowed_early_days("STANDARD") == 2
        assert allowed_early_days("  Generous  ") == 7


class TestComputeNextEligible:
    def test_basic_default_plan(self):
        result = compute_next_eligible(date(2026, 1, 1), 30, "default")
        assert result.next_eligible == date(2026, 1, 31)
        assert result.early_days == 0
        assert result.days_supply == 30

    def test_standard_plan_pulls_date_earlier(self):
        result = compute_next_eligible(date(2026, 1, 1), 30, "standard")
        # 30 days supply, 2 days early allowed -> eligible 2 days sooner
        assert result.next_eligible == date(2026, 1, 29)

    def test_generous_plan(self):
        result = compute_next_eligible(date(2026, 1, 1), 30, "generous")
        assert result.next_eligible == date(2026, 1, 24)

    def test_plan_with_zero_early_days_is_explicit(self):
        # "default" plan has 0 early days -- verify this is not a fallback
        # artifact but an intentional, testable zero.
        result = compute_next_eligible(date(2026, 3, 15), 90, "default")
        assert result.early_days == 0
        assert result.next_eligible == date(2026, 3, 15) + __import__(
            "datetime"
        ).timedelta(days=90)

    def test_leap_year_february_crossing(self):
        # 2026 is a leap year is WRONG -- 2026 is not a leap year (not div
        # by 4). Use 2028, a genuine leap year, days_supply crossing Feb 29.
        result = compute_next_eligible(date(2028, 2, 1), 30, "default")
        # Feb 2028 has 29 days. Jan 1 + 30 days should land correctly
        # accounting for the leap day.
        assert result.next_eligible == date(2028, 3, 2)

    def test_leap_year_exact_feb_29_boundary(self):
        result = compute_next_eligible(date(2028, 1, 30), 30, "default")
        assert result.next_eligible == date(2028, 2, 29)

    def test_non_leap_year_february_has_28_days(self):
        # 2027 is not a leap year.
        result = compute_next_eligible(date(2027, 1, 30), 30, "default")
        assert result.next_eligible == date(2027, 3, 1)

    def test_missing_days_supply_raises(self):
        with pytest.raises(EligibilityError, match="days_supply is required"):
            compute_next_eligible(date(2026, 1, 1), None, "default")

    def test_zero_days_supply_raises(self):
        with pytest.raises(EligibilityError, match="must be positive"):
            compute_next_eligible(date(2026, 1, 1), 0, "default")

    def test_negative_days_supply_raises(self):
        with pytest.raises(EligibilityError, match="must be positive"):
            compute_next_eligible(date(2026, 1, 1), -5, "default")

    def test_non_int_days_supply_raises(self):
        with pytest.raises(EligibilityError, match="must be an int"):
            compute_next_eligible(date(2026, 1, 1), "30", "default")  # type: ignore[arg-type]

    def test_bool_days_supply_raises(self):
        # bool is a subclass of int in Python; explicitly reject it since
        # "True" days supply is a type-confusion bug, not a valid value.
        with pytest.raises(EligibilityError, match="must be an int"):
            compute_next_eligible(date(2026, 1, 1), True, "default")  # type: ignore[arg-type]

    def test_missing_last_fill_date_raises(self):
        with pytest.raises(EligibilityError, match="must be a date"):
            compute_next_eligible("2026-01-01", 30, "default")  # type: ignore[arg-type]

    def test_none_plan_uses_default_early_days(self):
        result = compute_next_eligible(date(2026, 1, 1), 30, None)
        assert result.early_days == 0
        assert result.plan == "default"

    def test_result_to_dict(self):
        result = compute_next_eligible(date(2026, 1, 1), 30, "standard")
        d = result.to_dict()
        assert d["next_eligible"] == "2026-01-29"
        assert d["last_fill_date"] == "2026-01-01"
        assert d["days_supply"] == 30
        assert d["early_days"] == 2
        assert d["plan"] == "standard"
