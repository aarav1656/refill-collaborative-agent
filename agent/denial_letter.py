"""Denial letter parsing: pulls structured fields out of an uploaded denial
letter's text.

Deterministic regex/heuristic extraction, not a model call -- the fields we
pull here (medication, NDC, plan, denial reason, last fill date, days
supply if stated) feed the calculator and the packet, so they need to be
reproducible, not "vibes". If a field can't be found, we leave it None and
the agent asks a clarifying question for it (spec 02: "asks clarifying
questions -- plan, days supply, prior attempts").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass
class ParsedDenialLetter:
    medication: Optional[str] = None
    ndc: Optional[str] = None
    plan: Optional[str] = None
    denial_reason: Optional[str] = None
    last_fill_date: Optional[date] = None
    days_supply: Optional[int] = None
    raw_text: str = ""

    def to_dict(self) -> dict:
        return {
            "medication": self.medication,
            "ndc": self.ndc,
            "plan": self.plan,
            "denial_reason": self.denial_reason,
            "last_fill_date": self.last_fill_date.isoformat()
            if self.last_fill_date
            else None,
            "days_supply": self.days_supply,
        }

    def missing_fields(self) -> list[str]:
        """Which fields still need a clarifying question."""
        missing = []
        if not self.plan:
            missing.append("plan")
        if self.days_supply is None:
            missing.append("days_supply")
        if not self.last_fill_date:
            missing.append("last_fill_date")
        return missing


_NDC_RE = re.compile(r"\bNDC[:\s#]*([0-9]{4,5}-[0-9]{3,4}-[0-9]{1,2})\b", re.I)
_MED_RE = re.compile(r"(?:Medication|Drug)[:\s]+([^\n]+?)\s*$", re.I | re.M)
_PLAN_RE = re.compile(r"(?:Plan|Payer)[:\s]+([A-Za-z0-9 \-]+?)(?:\n|,|\.|$)", re.I)
_REASON_RE = re.compile(
    r"(?:Reason for denial|Denial reason)[:\s]+(.+?)(?:\n\s*\n|\Z)", re.I | re.S
)
_LAST_FILL_RE = re.compile(
    r"(?:Last fill(?:ed)? date|Date of last fill)[:\s]+"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
    re.I,
)
_DAYS_SUPPLY_RE = re.compile(r"Days?\s*supply[:\s]+(\d+)", re.I)


def _parse_date(raw: str) -> Optional[date]:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_denial_letter(text: str) -> ParsedDenialLetter:
    """Extract known fields from a denial letter's plain text.

    Missing fields stay None; the agent's clarifying-question flow fills
    them in via dialogue rather than guessing.
    """
    result = ParsedDenialLetter(raw_text=text)

    if m := _MED_RE.search(text):
        result.medication = m.group(1).strip()
    if m := _NDC_RE.search(text):
        result.ndc = m.group(1).strip()
    if m := _PLAN_RE.search(text):
        result.plan = m.group(1).strip()
    if m := _REASON_RE.search(text):
        result.denial_reason = re.sub(r"\s+", " ", m.group(1)).strip()
    if m := _LAST_FILL_RE.search(text):
        result.last_fill_date = _parse_date(m.group(1).strip())
    if m := _DAYS_SUPPLY_RE.search(text):
        result.days_supply = int(m.group(1))

    return result
