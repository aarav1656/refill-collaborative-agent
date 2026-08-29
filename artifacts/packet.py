"""One-page PDF packet: the artifact that outlives the request.

Contains medication, NDC, plan, last fill date, days supply, the
CALCULATOR-derived next eligible date (never the model's claim), the
denial reason, and an exact phone script. reportlab, no external deps
beyond what's already installed in .venv.

This module never runs unless the validator has already PASSED (agentspine
run_tick only calls artifact_fn on a passing verdict) -- so by construction
a packet's "next eligible date" is always the calculator's date, is never
producible from a rejected run.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from typing import Optional

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib import colors


@dataclass
class PacketData:
    medication: str
    ndc: Optional[str]
    plan: str
    last_fill_date: date
    days_supply: int
    next_eligible_date: date  # MUST be calculator-derived, see build_packet_pdf
    denial_reason: str
    caregiver_name: Optional[str] = None
    parent_name: Optional[str] = None


def _phone_script(data: PacketData) -> list[str]:
    """Exact sentences to say on the phone. Deterministic, not model-written
    -- this is what a caregiver reads verbatim, so it must be predictable.
    """
    med = data.medication
    plan = data.plan
    next_date = data.next_eligible_date.strftime("%B %-d, %Y")
    return [
        f'"Hi, I\'m calling about a prior-authorization denial for {med} '
        f'under the {plan} plan."',
        f'"The denial letter states: {data.denial_reason}."',
        f'"According to the days-supply on the last fill ({data.days_supply} days, '
        f'filled {data.last_fill_date.strftime("%B %-d, %Y")}), the plan\'s own '
        f'refill-too-soon rule makes this refill eligible on {next_date}."',
        '"Can you confirm that date is correct on your end, and tell me '
        'what happens if I call back on or after that date?"',
        '"If there is an appeal process for an early refill exception, '
        'can you send me the form or tell me how to start it?"',
    ]


def build_packet_pdf(data: PacketData) -> bytes:
    """Render the one-page packet to PDF bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "PacketTitle", parent=styles["Title"], fontSize=16, spaceAfter=4
    )
    heading_style = ParagraphStyle(
        "PacketHeading", parent=styles["Heading2"], fontSize=11, spaceBefore=10
    )
    body_style = styles["BodyText"]
    script_style = ParagraphStyle(
        "PhoneScript", parent=styles["BodyText"], leftIndent=14, spaceAfter=6
    )

    story = []
    story.append(Paragraph("Refill Chase Packet", title_style))
    story.append(Paragraph(
        "Prepared for a call to the payer. This packet does not submit "
        "anything on your behalf.", body_style
    ))

    fact_rows = [
        ["Medication", data.medication],
        ["NDC", data.ndc or "not provided"],
        ["Plan", data.plan],
        ["Last fill date", data.last_fill_date.isoformat()],
        ["Days supply", str(data.days_supply)],
        ["Next eligible date (calculator)", data.next_eligible_date.isoformat()],
        ["Denial reason", data.denial_reason],
    ]
    table = Table(fact_rows, colWidths=[2.2 * inch, 4.2 * inch])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(Spacer(1, 8))
    story.append(table)

    story.append(Paragraph("Phone script", heading_style))
    for line in _phone_script(data):
        story.append(Paragraph(line, script_style))

    doc.build(story)
    return buf.getvalue()
