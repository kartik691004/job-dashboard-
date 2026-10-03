"""Phase 30.1 — company-header resolver coverage tests.

Narrow addition: explicitly labeled company-header lines ("🏢 Company: Flipkart")
resolve as explicit POST_TEXT evidence. Everything else must keep failing closed
exactly as before: emails, prose, author names, agencies, clients, hashtags,
URLs, generic descriptors, empty values.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.enrichment.company_resolver import (
    _company_header,
    _explicit_in_post,
    resolve,
)
from app.enrichment.schemas import CompanyEvidence

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "data" / "validation" / "phase26_fresh_run"


def _raw_text(url_fragment: str) -> str:
    raw = json.loads((RUN_DIR / "raw_dataset.json").read_text(encoding="utf-8"))
    for it in raw["items"]:
        if isinstance(it, dict) and url_fragment in (it.get("post_url") or ""):
            return it.get("text") or ""
    raise AssertionError(f"dataset item not found: {url_fragment}")


# ── POSITIVE: explicit company headers ──────────────────────────────────────

def test_header_emoji_colon():
    assert _company_header("🏢 Company: Flipkart") == "Flipkart"


def test_header_plain_colon():
    assert _company_header("Company: Flipkart") == "Flipkart"


def test_header_uppercase_label():
    assert _company_header("COMPANY: Flipkart") == "Flipkart"


def test_header_surrounding_emoji_and_whitespace():
    assert _company_header("  ✅  Company:  Vyapar  ") == "Vyapar"


def test_header_leading_symbols_before_label():
    assert _company_header("📌👉 Company: Citi") == "Citi"


def test_header_value_with_legal_suffix():
    assert _company_header(
        "Company: Supertails.com India Pvt. Ltd") == "Supertails.com India Pvt. Ltd"


def test_header_value_stops_at_pipe_field_boundary():
    assert _company_header("Company: Vyapar | Bangalore") == "Vyapar"


def test_header_value_stops_at_hashtag_boundary():
    assert _company_header("Company: Battery Smart #ev") == "Battery Smart"


def test_header_value_stops_at_dash_boundary():
    assert _company_header("Company: Citi – Gurugram") == "Citi"


def test_header_value_bounded_to_single_line():
    text = "Role: Data Analyst\n🏢 Company: Flipkart\nLocation: Jaipur\nExperience: 0-2 yrs"
    assert _company_header(text) == "Flipkart"


def test_resolve_header_is_post_text_tier():
    r = resolve("🏢 Company: Flipkart\nRole: Data Analyst", "", "", "")
    assert r.company == "Flipkart"
    assert r.company_evidence is CompanyEvidence.POST_TEXT


# ── NEGATIVE: must keep failing closed ──────────────────────────────────────

def test_email_line_never_yields_company():
    assert _company_header("please share your profile at rajesh.fulvari@flipkart.com") is None
    assert _explicit_in_post(
        "share your profile at rajesh.fulvari@flipkart.com") is None


def test_prose_company_word_never_yields_company():
    assert _company_header("The company is growing fast: Acme Corp") is None
    assert _company_header("a great company: Acme Corp") is None


def test_my_company_is_never_a_header():
    assert _company_header("my company is Acme Corp") is None
    assert _company_header("My company is Acme Corp") is None
    assert _company_header("our company: Zoho Corp") is None


def test_author_name_label_never_yields_company():
    # A person's name as the line label is not a company field.
    assert _company_header("Rajesh Fulvari: hiring Data Analysts in Jaipur") is None


def test_agency_recruiter_name_label_never_yields_company():
    assert _company_header("Zenrixi HR Solutions: we are hiring") is None
    assert _company_header("Hiredoor-In: Data Analyst opening") is None


def test_client_list_label_never_yields_company():
    assert _company_header("Clients: Fractal, Tredence, Mu Sigma") is None
    assert _company_header("working with clients: Fractal") is None


def test_hashtag_lines_never_yield_company():
    assert _company_header("#Hiring #Flipkart #DataAnalytics #Company") is None
    assert _company_header("#Hiring #Company: Flipkart") is None


def test_url_lines_never_yield_company():
    assert _company_header("apply at https://www.flipkart.com/careers") is None
    assert _company_header("careers page: https://www.flipkart.com") is None
    assert _company_header("https://company.example.com/job") is None


def test_generic_descriptor_value_rejected():
    assert _company_header("Company: a leading fintech startup") is None


def test_empty_or_placeholder_value_rejected():
    assert _company_header("Company: ") is None
    assert _company_header("Company: -") is None
    assert _company_header("Company: unclear") is None
    assert _company_header("Company: N/A") is None


def test_lowercase_value_rejected_fail_closed():
    assert _company_header("Company: devx") is None


def test_invalid_header_value_falls_through_to_prose_patterns():
    # Header rejected -> pre-existing prose extraction still applies.
    text = "Company: unclear\nWe are hiring for Data Analyst at Zoho Corp"
    assert _explicit_in_post(text) == "Zoho Corp"


# ── PIN: existing precedence / tier logic unchanged ─────────────────────────

def test_join_still_beats_header_when_both_present():
    text = "Join Acme Corp, an AI-native platform\nCompany: Beta Ltd"
    assert _explicit_in_post(text) == "Acme Corp"


def test_authoritative_job_metadata_still_beats_header():
    r = resolve("🏢 Company: Flipkart", "Amazon", "", "",
                metadata_is_authoritative=True)
    assert r.company == "Amazon"
    assert r.company_evidence is CompanyEvidence.JOB_METADATA


# ── PIN: Phase 30 frozen reproductions (verbatim Phase26 dataset) ───────────

def test_flipkart_reproduction_now_resolves():
    text = _raw_text("rajesh-fulvari-081a61115_hiring-flipkart-dataanalytics")
    assert "🏢 Company: Flipkart" in text
    r = resolve(text, "", "", "")
    assert r.company == "Flipkart"
    assert r.company_evidence is CompanyEvidence.POST_TEXT


def test_yrcs_prose_mention_stays_unclear():
    # YRCS names the employer only in prose ("About YR: Consultancy Services"),
    # never in an explicit Company header — must keep failing closed.
    text = _raw_text("yrcs_about-yr-consultancy-services")
    r = resolve(text, "", "", "")
    assert r.company == "Unclear"
    assert r.company_evidence is CompanyEvidence.UNCLEAR


def test_battery_smart_reproduction_now_resolves():
    text = _raw_text("zenrixi-hr-solutions-82a1b03bb_zenrixi-hiring-vacancy")
    r = resolve(text, "", "", "")
    assert r.company == "Battery Smart"
    assert r.company_evidence is CompanyEvidence.POST_TEXT


def test_flipkart_header_line_is_the_only_new_signal():
    # Sanity: without the header line the old behavior (Unclear) would persist;
    # the resolved value must come from the labeled line, not other prose.
    text = _raw_text("rajesh-fulvari-081a61115_hiring-flipkart-dataanalytics")
    stripped = "\n".join(ln for ln in text.splitlines()
                         if ln.strip().lower() != "🏢 company: flipkart")
    r = resolve(stripped, "", "", "")
    assert r.company == "Unclear"
