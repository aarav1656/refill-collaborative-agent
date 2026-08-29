"""Tests for agent/denial_letter.py -- deterministic extraction, no model."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from agent.denial_letter import parse_denial_letter

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_denial_letter.txt"


def test_parses_sample_denial_letter_fully():
    text = FIXTURE.read_text()
    result = parse_denial_letter(text)

    assert result.medication == "Enbrel (etanercept) 50mg/mL"
    assert result.ndc == "58406-0435-1"
    assert result.plan == "Standard"
    assert "Refill too soon" in result.denial_reason
    assert result.last_fill_date == date(2026, 1, 1)
    assert result.days_supply == 30


def test_sample_fixture_is_clearly_marked_synthetic():
    text = FIXTURE.read_text()
    assert "SYNTHETIC" in text
    assert "SAMPLE" in text


def test_missing_fields_stay_none():
    result = parse_denial_letter("Just some unrelated text with no fields.")
    assert result.medication is None
    assert result.ndc is None
    assert result.plan is None
    assert result.days_supply is None
    assert result.last_fill_date is None


def test_missing_fields_reports_all_required_as_missing():
    result = parse_denial_letter("Nothing useful here.")
    missing = result.missing_fields()
    assert "plan" in missing
    assert "days_supply" in missing
    assert "last_fill_date" in missing


def test_partial_letter_only_flags_actual_gaps():
    text = "Plan: Standard\nDays supply: 30\n"
    result = parse_denial_letter(text)
    missing = result.missing_fields()
    assert "plan" not in missing
    assert "days_supply" not in missing
    assert "last_fill_date" in missing


def test_slash_date_format_parses():
    text = "Last fill date: 01/15/2026\n"
    result = parse_denial_letter(text)
    assert result.last_fill_date == date(2026, 1, 15)


def test_to_dict_serializes_date_as_iso():
    result = parse_denial_letter(FIXTURE.read_text())
    d = result.to_dict()
    assert d["last_fill_date"] == "2026-01-01"
    assert d["days_supply"] == 30
