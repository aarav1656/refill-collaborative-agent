"""Tests for artifacts/packet.py -- the PDF is real, one page, and its
next-eligible date field always traces back to what the caller passed as
next_eligible_date (which, by construction in job/tick.py, is always the
calculator's date -- never the model's).
"""

from __future__ import annotations

from datetime import date

from pypdf import PdfReader
import io

from artifacts.packet import PacketData, build_packet_pdf


def _sample_data() -> PacketData:
    return PacketData(
        medication="Enbrel (etanercept) 50mg/mL",
        ndc="58406-0435-1",
        plan="standard",
        last_fill_date=date(2026, 1, 1),
        days_supply=30,
        next_eligible_date=date(2026, 1, 29),
        denial_reason="Refill too soon.",
    )


def test_packet_is_valid_single_page_pdf():
    pdf_bytes = build_packet_pdf(_sample_data())
    assert pdf_bytes.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1


def test_packet_contains_all_required_fields():
    pdf_bytes = build_packet_pdf(_sample_data())
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = reader.pages[0].extract_text()

    assert "Enbrel" in text
    assert "58406-0435-1" in text
    assert "standard" in text
    assert "2026-01-01" in text  # last fill
    assert "30" in text  # days supply
    assert "2026-01-29" in text  # calculator-derived next eligible date
    assert "Refill too soon" in text


def test_packet_contains_phone_script_sentences():
    pdf_bytes = build_packet_pdf(_sample_data())
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = reader.pages[0].extract_text()
    assert "calling about a prior-authorization denial" in text
    assert "January 29, 2026" in text  # human-readable date in the script
