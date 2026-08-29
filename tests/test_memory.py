"""Tests for memory/profile.py -- InMemoryMemoryService, including the
mandatory delete/correct path (spec 02: "a memory demo without deletion is
a governance fail").
"""

from __future__ import annotations

from memory.profile import InMemoryMemoryService, profile_key


def test_profile_key_is_deterministic_and_case_insensitive():
    assert profile_key("Alice", "Standard") == profile_key("alice", "standard")
    assert profile_key("alice", "standard") == "alice::standard"


def test_remember_and_retrieve_fact():
    m = InMemoryMemoryService()
    m.remember_fact("alice", "standard", "plan_quirk", "needs prior auth every 90 days")
    profile = m.get_profile("alice", "standard")
    assert profile is not None
    assert profile.active_facts()["plan_quirk"] == "needs prior auth every 90 days"


def test_get_profile_none_when_never_remembered():
    m = InMemoryMemoryService()
    assert m.get_profile("nobody", "standard") is None


def test_forget_fact_removes_from_active_facts():
    m = InMemoryMemoryService()
    m.remember_fact("alice", "standard", "plan", "standard")
    assert "plan" in m.get_profile("alice", "standard").active_facts()

    deleted = m.forget_fact("alice", "standard", "plan")
    assert deleted is True
    assert "plan" not in m.get_profile("alice", "standard").active_facts()


def test_forget_fact_returns_false_when_nothing_to_delete():
    m = InMemoryMemoryService()
    assert m.forget_fact("alice", "standard", "nonexistent") is False


def test_forget_fact_is_idempotent():
    m = InMemoryMemoryService()
    m.remember_fact("alice", "standard", "plan", "standard")
    assert m.forget_fact("alice", "standard", "plan") is True
    # Second delete of an already-deleted fact reports nothing-to-do.
    assert m.forget_fact("alice", "standard", "plan") is False


def test_correct_fact_overwrites_value():
    m = InMemoryMemoryService()
    m.remember_fact("alice", "standard", "days_supply", 30)
    m.correct_fact("alice", "standard", "days_supply", 90)
    assert m.get_profile("alice", "standard").active_facts()["days_supply"] == 90


def test_correct_fact_raises_on_unknown_key():
    import pytest
    m = InMemoryMemoryService()
    with pytest.raises(KeyError):
        m.correct_fact("alice", "standard", "nonexistent", "value")


def test_deleted_fact_leaves_no_trace_in_active_facts_but_record_exists():
    """Deletion is a visible state change (audit trail preserved), not
    silently erasing the record entirely -- but it must be GONE from what
    the agent actually uses (active_facts)."""
    m = InMemoryMemoryService()
    m.remember_fact("alice", "standard", "plan", "standard")
    m.forget_fact("alice", "standard", "plan")
    profile = m.get_profile("alice", "standard")
    assert "plan" not in profile.active_facts()
    assert profile.facts["plan"].deleted is True  # audit trail retained


def test_two_different_profiles_are_isolated():
    m = InMemoryMemoryService()
    m.remember_fact("alice", "standard", "plan", "standard")
    m.remember_fact("bob", "generous", "plan", "generous")
    assert m.get_profile("alice", "standard").active_facts()["plan"] == "standard"
    assert m.get_profile("bob", "generous").active_facts()["plan"] == "generous"
