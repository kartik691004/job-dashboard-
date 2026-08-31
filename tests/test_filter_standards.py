"""
test_filter_standards.py — core logic of tools/filter_sheet_by_standards.py.

Pure functions only; no network. Verifies that rows are judged by the CURRENT
classifier standards — in particular that compensation-only India evidence
(Rs / LPA / CTC) is no longer accepted, while city evidence still is.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sheets_writer import EXPECTED_HEADERS
from tools.filter_sheet_by_standards import filter_rows, row_to_rawpost


def make_row(over: dict = None) -> list:
    row = {name: "" for name in EXPECTED_HEADERS}
    row.update({
        "Company Name": "Acme",
        "Major Category": "Chief of Staff",
        "Exact Role": "Chief of Staff",
        "Source Link": "https://www.linkedin.com/posts/acme-1",
        "Description": "",
        "Status": "New",
    })
    row.update(over or {})
    return [row[name] for name in EXPECTED_HEADERS]


def sheet_with(*rows) -> list:
    return [list(EXPECTED_HEADERS)] + [list(r) for r in rows]


def test_row_to_rawpost_maps_description_and_source():
    raw = row_to_rawpost(EXPECTED_HEADERS, make_row())
    assert raw.post_url == "https://www.linkedin.com/posts/acme-1"
    assert raw.company == "Acme"


def test_comp_only_row_drops_under_new_standard():
    """CTC/LPA alone used to pass as an Indian company; now it must drop."""
    values = sheet_with(make_row({
        "Description": "Hiring a Chief of Staff, CTC 20 LPA. Apply now.",
    }))
    kept, dropped = filter_rows(values)
    assert len(kept) == 1  # header only
    assert len(dropped) == 1
    assert "unclear india relevance" in dropped[0]["reason"].lower()
    assert dropped[0]["source_link"] == "https://www.linkedin.com/posts/acme-1"


def test_city_row_still_kept():
    values = sheet_with(make_row({
        "Description": "We are hiring a Chief of Staff in Bangalore. Apply now.",
    }))
    kept, dropped = filter_rows(values)
    assert len(kept) == 2 and not dropped


def test_non_india_row_drops():
    values = sheet_with(make_row({
        "Description": "We are hiring a Chief of Staff in London. Apply now.",
    }))
    kept, dropped = filter_rows(values)
    assert len(kept) == 1 and len(dropped) == 1
    assert "not india" in dropped[0]["reason"].lower()


def test_blank_padding_rows_are_ignored():
    values = sheet_with(
        make_row({"Description": "We are hiring a Chief of Staff in Pune. Apply now."}),
        [""] * len(EXPECTED_HEADERS),
    )
    kept, dropped = filter_rows(values)
    assert len(kept) == 2 and not dropped


def test_internship_row_drops():
    values = sheet_with(make_row({
        "Description": "Internship opening: Chief of Staff intern in Mumbai.",
    }))
    kept, dropped = filter_rows(values)
    assert len(kept) == 1 and len(dropped) == 1
    assert "internship" in dropped[0]["reason"].lower()


def test_kept_rows_preserved_byte_for_byte():
    row = make_row({"Description": "We are hiring a Founder's Office Associate in Noida. DM to apply."})
    values = sheet_with(row)
    kept, _ = filter_rows(values)
    assert kept[1] == list(row)
