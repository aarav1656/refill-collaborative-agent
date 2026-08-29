"""Tests for agent/dialogue.py -- deterministic clarifying-question flow
and the "second session is shorter because of memory" mechanism.
"""

from __future__ import annotations

from datetime import date

from agent.denial_letter import parse_denial_letter
from agent.dialogue import DialogueSession
from memory.profile import InMemoryMemoryService

SAMPLE_LETTER = """
Medication: Enbrel
Plan: Standard
Days supply: 30
Last fill date: 2026-01-01
Reason for denial: Refill too soon.
"""

INCOMPLETE_LETTER = """
Medication: Enbrel
Reason for denial: Refill too soon.
"""


def test_fully_specified_letter_needs_no_questions():
    parsed = parse_denial_letter(SAMPLE_LETTER)
    session = DialogueSession(user_id="u1")
    session.prefill_from_letter(parsed)
    assert session.is_ready_for_eligibility()
    assert session.next_question_field() == "prior_attempts"


def test_incomplete_letter_asks_for_missing_required_fields_in_order():
    parsed = parse_denial_letter(INCOMPLETE_LETTER)
    session = DialogueSession(user_id="u1")
    session.prefill_from_letter(parsed)
    assert not session.is_ready_for_eligibility()

    # Required fields asked in declared order: plan, days_supply, last_fill_date
    assert session.next_question_field() == "plan"
    session.mark_asked("plan")
    session.answer("plan", "standard")

    assert session.next_question_field() == "days_supply"
    session.mark_asked("days_supply")
    session.answer("days_supply", 30)

    assert session.next_question_field() == "last_fill_date"
    session.mark_asked("last_fill_date")
    session.answer("last_fill_date", "2026-01-01")

    assert session.is_ready_for_eligibility()


def test_never_asks_same_field_twice_in_one_session():
    session = DialogueSession(user_id="u1")
    assert session.next_question_field() == "plan"
    session.mark_asked("plan")
    # Still unanswered, but already asked -- move to next field instead of
    # re-asking (a caregiver who ignored the question shouldn't be nagged
    # with an identical prompt).
    assert session.next_question_field() == "days_supply"


def test_second_session_is_shorter_because_memory_prefills_plan_quirk():
    memory = InMemoryMemoryService()
    memory.remember_fact("caregiver1", "standard", "plan", "standard")
    memory.remember_fact("caregiver1", "standard", "days_supply", 30)

    # First session: nothing remembered yet, needs all 3 required questions.
    first_session = DialogueSession(user_id="caregiver1")
    first_session_questions = 0
    while (field := first_session.next_question_field()) is not None:
        if field in ("plan", "days_supply", "last_fill_date"):
            first_session_questions += 1
            first_session.mark_asked(field)
            first_session.answer(field, "dummy")
        else:
            first_session.mark_asked(field)

    # Second session for the same profile: memory prefills plan + days_supply.
    profile = memory.get_profile("caregiver1", "standard")
    second_session = DialogueSession(user_id="caregiver1")
    second_session.prefill_from_memory(profile)
    second_session_questions = 0
    while (field := second_session.next_question_field()) is not None:
        if field in ("plan", "days_supply", "last_fill_date"):
            second_session_questions += 1
            second_session.mark_asked(field)
            second_session.answer(field, "dummy")
        else:
            second_session.mark_asked(field)

    assert second_session_questions < first_session_questions
    assert second_session_questions == 1  # only last_fill_date still unknown


def test_eligibility_context_converts_iso_string_date():
    session = DialogueSession(user_id="u1")
    session.answer("last_fill_date", "2026-01-01")
    session.answer("days_supply", 30)
    session.answer("plan", "standard")
    ctx = session.eligibility_context()
    assert ctx["last_fill_date"] == date(2026, 1, 1)
    assert ctx["days_supply"] == 30
    assert ctx["plan"] == "standard"
