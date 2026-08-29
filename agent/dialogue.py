"""Dialogue state machine: the multi-turn clarifying-question flow.

Deterministic (no model call) -- decides WHICH clarifying questions still
need asking given what the denial letter parse already found and what
profile memory already remembers. This is what makes "second session
visibly shorter" a testable, reproducible fact rather than a vibe: fewer
missing fields -> fewer questions -> a shorter session, provably.

The model (agent/refill_agent.py) is responsible for the natural-language
phrasing of a question; this module is responsible for WHICH questions
exist and WHEN the flow is ready to hand off to the eligibility
calculator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from agent.denial_letter import ParsedDenialLetter
from memory.profile import ProfileMemory

# Fields required before the eligibility calculator can run.
REQUIRED_FIELDS = ("plan", "days_supply", "last_fill_date")

# Fields worth asking about even once eligibility is computable, because
# the spec calls for them explicitly ("prior attempts").
OPTIONAL_FIELDS = ("prior_attempts",)


@dataclass
class DialogueSession:
    """Tracks what's known so far for one chase (one user, one run).

    known: field name -> value, merged from (in priority order) explicit
    user answers this session > profile memory > denial letter parse.
    asked: which fields we've already prompted for this session, so we
    never ask the same clarifying question twice in one run.
    """

    user_id: str
    known: dict[str, Any] = field(default_factory=dict)
    asked: set[str] = field(default_factory=set)
    turns: int = 0

    def prefill_from_letter(self, parsed: ParsedDenialLetter) -> None:
        for f in ("medication", "ndc", "plan", "denial_reason",
                   "last_fill_date", "days_supply"):
            value = getattr(parsed, f)
            if value is not None and f not in self.known:
                self.known[f] = value

    def prefill_from_memory(self, profile: Optional[ProfileMemory]) -> None:
        """Prefill from remembered facts. This is the "second session is
        shorter" mechanism: any field remembered from a prior chase for
        this user+plan skips its clarifying question entirely."""
        if profile is None:
            return
        for key, value in profile.active_facts().items():
            if key not in self.known:
                self.known[key] = value

    def answer(self, field_name: str, value: Any) -> None:
        self.known[field_name] = value
        self.turns += 1

    def next_question_field(self) -> Optional[str]:
        """Which field to ask about next, or None if nothing outstanding.

        Required fields take priority over optional ones; within a
        priority tier, fields are asked in declaration order so runs are
        reproducible.
        """
        for f in REQUIRED_FIELDS:
            if f not in self.known and f not in self.asked:
                return f
        for f in OPTIONAL_FIELDS:
            if f not in self.known and f not in self.asked:
                return f
        return None

    def mark_asked(self, field_name: str) -> None:
        self.asked.add(field_name)

    def is_ready_for_eligibility(self) -> bool:
        """True once every REQUIRED_FIELDS is known (asked or not -- if a
        caregiver declines to answer, the run stays blocked; we never
        silently proceed with a guessed value)."""
        return all(f in self.known for f in REQUIRED_FIELDS)

    def questions_asked_count(self) -> int:
        return len(self.asked)

    def eligibility_context(self) -> dict:
        """Build the context dict the EligibilityValidator expects."""
        last_fill = self.known.get("last_fill_date")
        if isinstance(last_fill, str):
            last_fill = date.fromisoformat(last_fill)
        return {
            "last_fill_date": last_fill,
            "days_supply": self.known.get("days_supply"),
            "plan": self.known.get("plan"),
            "model_claimed_date": self.known.get("model_claimed_date"),
        }
