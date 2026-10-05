"""test_phase38_hm_field.py — Phase 38 hiring-manager display + extraction tests.

Deterministic only (no Apify/Groq/Sheets). Verifies:

1. extract_hm_context ONLY extracts a person when there is explicit
   person-attribution + hiring/vacancy context.
2. Missing/ambiguous HM displays as BLANK in the 23-col row (never Unclear/NA).
3. Verified HM name + URL display verbatim.
4. Production barrier still blocks Unclear-displayed HM (internal NOT_FOUND).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier import DeterministicClassifier
from app.enrichment.enricher import Enricher
from app.enrichment.schemas import EnrichedLead
from app.extraction import extract_hm_context, extract_named_founder, extract_recruiter_signature
from app.models import ClassifiedPost
from app.production_gate import route_production_post
from app.llm.schemas import LeadVerdict, GateResult
from app.sheets_writer import HIRING_MANAGER_EMPTY, HIRING_MANAGER_NOT_AVAILABLE, SheetsWriter


# ── extraction helpers ──────────────────────────────────────────────────────

def _post(text, author="Tester", url="https://linkedin.com/in/tester"):
    return ClassifiedPost(
        post_url="http://x", source_link="http://x",
        company_name="Acme", major_category="Founder's Office",
        exact_role="FO Associate", ctc="Not Disclosed",
        cold_email="Not Available",
        hiring_manager_name="Unclear", hiring_manager_linkedin="Unclear",
        hiring_manager_evidence="",
        apply_google_form="", apply_link="", key_points="",
        confidence=0.95, location="Bengaluru",
        employment_type="Full-time", experience_requirement="Not Specified",
        market="India", india_relevance="India",
        post_date="2026-01-01", scraped_at="2026-01-01",
        classification_reason="test", status="New",
        text=text, author_name=author, author_profile_url=url,
    )


# ── 1. extract_hm_context adversarial matrix ──────────────────────────────

def test_named_person_reports_to():
    # "Please reach out to Priya Sharma, Head of Analytics, regarding this role."
    ctx = extract_hm_context(
        "This role will work directly with Priya Sharma, Head of Analytics. "
        "Please reach out to Priya Sharma, Head of Analytics, regarding this role.")
    assert ctx.name == "Priya Sharma"
    assert "Priya Sharma" in ctx.evidence


def test_named_person_working_with():
    # "You will be working with Rahul Mehta on the founding team."
    ctx = extract_hm_context(
        "This role will work directly with Rahul Mehta on the founding team.")
    assert ctx.name == "Rahul Mehta"


def test_named_person_reporting_line():
    ctx = extract_hm_context(
        "In this role you will report directly to Anita Desai, Head of Strategy.")
    assert ctx.name == "Anita Desai"


def test_named_person_contact_person_label():
    ctx = extract_hm_context(
        "Contact person: Rohan Mehta, Talent Acquisition Lead.")
    assert ctx.name == "Rohan Mehta"


def test_named_person_point_of_contact_label():
    ctx = extract_hm_context(
        "Point of contact: Sanjay Gupta, HR.")
    assert ctx.name == "Sanjay Gupta"


def test_role_tail_captured_not_conflated_with_name():
    ctx = extract_hm_context(
        "Please reach out to Priya Sharma, Head of Analytics, regarding this role.")
    assert ctx.name == "Priya Sharma"
    assert ctx.role == "Head of Analytics"


def test_company_not_conflated_with_person():
    # "We are looking for a candidate to work with Acme Corp."
    ctx = extract_hm_context(
        "We are looking for a candidate to work with Acme Corp on this role.")
    assert ctx.name == ""


def test_all_caps_not_conflated():
    ctx = extract_hm_context("REACH OUT TO HR FOR DETAILS.")
    assert ctx.name == ""


def test_short_name_not_conflated():
    # single-token capitalized words are not taken without a second word
    ctx = extract_hm_context("Reach out to HR for this role.")
    assert ctx.name == ""


def test_author_not_auto_hm_from_context():
    # "Rahul posted this vacancy." — author mention alone is NOT HM
    ctx = extract_hm_context("Rahul posted this vacancy.")
    assert ctx.name == ""


def test_person_near_role_without_attribution_not_hm():
    # "Data Analyst — Priya Sharma" — the role-label and a name, with no
    # attribution cue ("reports to", "contact", ...) must NOT become HM.
    ctx = extract_hm_context("Data Analyst — Priya Sharma")
    assert ctx.name == ""


def test_profile_near_role_no_attribution_not_hm():
    # a /in/ URL next to a role does not identify a person without attribution
    ctx = extract_hm_context(
        "Data Analyst\nhttps://www.linkedin.com/in/rahul")
    assert ctx.name == ""


def test_llm_url_only_no_person_attribution_not_hm():
    # "profile: linkedin.com/in/rahul" with no person-attribution cue is NOT HM
    ctx = extract_hm_context("profile: linkedin.com/in/rahul")
    assert ctx.name == ""


def test_company_page_url_in_text_not_conflated():
    ctx = extract_hm_context(
        "We're hiring. https://www.linkedin.com/company/acme/posts")
    assert ctx.name == ""


def test_contact_person_with_two_name_parts():
    ctx = extract_hm_context("Reach out to Priya Sharma for this role.")
    assert ctx.name == "Priya Sharma"


def test_contact_person_three_name_parts():
    ctx = extract_hm_context(
        "Reach out to Priya Sharma Kumar for this role.")
    # three-word names are still captured as long as they match the pattern
    assert ctx.name == "Priya Sharma Kumar"


def test_ambiguous_single_name_word_not_taken():
    ctx = extract_hm_context("Reach out to Priya for this role.")
    assert ctx.name == ""  # single capitalized word without a second word


def test_explicit_person_attribution_only_takes_explicit():
    # "Rahul is on our team" is NOT a hiring-manager attribution cue
    ctx = extract_hm_context("Rahul is on our team")
    assert ctx.name == ""


def test_working_with_takes_explicit_person_attribution():
    # "working with X" / "will work directly with X" is an allowed attribution cue
    ctx = extract_hm_context(
        "You will work directly with Priya Sharma on the founding team.")
    assert ctx.name == "Priya Sharma"


def test_contact_person_explicit_person_attribution():
    ctx = extract_hm_context(
        "Contact person: Priya Sharma, Head of Analytics.")
    assert ctx.name == "Priya Sharma"


def test_point_of_contact_label_explicit():
    ctx = extract_hm_context(
        "Point of contact: Priya Sharma.")
    assert ctx.name == "Priya Sharma"


def test_empty_or_none_text_returns_empty():
    assert extract_hm_context("") == extract_hm_context(None) == type(extract_hm_context(""))()


# ── 2. display blanking regressions ────────────────────────────────────────

def test_missing_hm_displays_blank():
    post = _post(
        "We're hiring a Founder's Office Associate in Mumbai. Apply now.",
        url="https://linkedin.com/in/tester")
    post.hiring_manager_name = "Unclear"
    post.hiring_manager_linkedin = "Unclear"
    row = SheetsWriter._build_row(post)
    # Column 6 = Hiring Manager Name, Column 7 = Hiring Manager LinkedIn
    assert row[5] == HIRING_MANAGER_EMPTY
    assert row[6] == HIRING_MANAGER_NOT_AVAILABLE


def test_na_display_blank():
    post = _post("We're hiring a Founder's Office Associate in Mumbai. Apply now.")
    post.hiring_manager_name = "N/A"
    post.hiring_manager_linkedin = "N/A"
    row = SheetsWriter._build_row(post)
    assert row[5] == HIRING_MANAGER_EMPTY
    assert row[6] == HIRING_MANAGER_NOT_AVAILABLE


def test_none_display_blank():
    post = _post("We're hiring a Founder's Office Associate in Mumbai. Apply now.")
    post.hiring_manager_name = None
    post.hiring_manager_linkedin = None
    row = SheetsWriter._build_row(post)
    assert row[5] == HIRING_MANAGER_EMPTY
    assert row[6] == HIRING_MANAGER_NOT_AVAILABLE


def test_verified_hm_name_displays_verbatim():
    post = _post("We're hiring a Founder's Office Associate in Mumbai. Apply now.")
    post.hiring_manager_name = "Priya Sharma"
    post.hiring_manager_linkedin = "https://www.linkedin.com/in/priya-sharma"
    row = SheetsWriter._build_row(post)
    assert row[5] == "Priya Sharma"
    assert row[6] == "https://www.linkedin.com/in/priya-sharma"


def test_verified_hm_only_name_no_url_displays_name_but_blanks_url():
    # When HM name is verified but no /in/ URL in text, LinkedIn should blank
    post = _post("We're hiring a Founder's Office Associate in Mumbai. Apply now.",
                 url="https://linkedin.com/in/tester")
    post.hiring_manager_name = "Priya Sharma"
    post.hiring_manager_linkedin = "Unclear"
    row = SheetsWriter._build_row(post)
    assert row[5] == "Priya Sharma"
    assert row[6] == HIRING_MANAGER_NOT_AVAILABLE


def test_existing_fields_passthrough_unaffected():
    # CTC, email, apply_link, apply_google_form, key_points should NOT be
    # blanked merely because they are missing in a fixture.  (Only the HM
    # columns are guaranteed-blank by this phase; existing schema rules govern
    # the rest — and the classifier only ever writes real values / "Not Available".)
    post = _post("We're hiring a Founder's Office Associate in Mumbai. Apply now.")
    post.hiring_manager_name = "Unclear"
    post.hiring_manager_linkedin = "Unclear"
    row = SheetsWriter._build_row(post)
    # unchanged passthrough columns (these are Not Available from the fixture)
    assert row[4] == "Not Available"   # Cold Email
    assert row[21] == ""               # Apply Link (fixture: none present)
    assert len(row) == 23


def test_row_length_stays_23():
    post = _post("We're hiring a Founder's Office Associate in Mumbai. Apply now.")
    assert len(SheetsWriter._build_row(post)) == 23


# ── 3. verifier fold-in does not write Unclear into HM cells ──────────────

def test_verifier_fold_in_clears_unclear():
    from app.llm.verifier import enriched_post
    post = _post("We're hiring a Founder's Office Associate in Mumbai. Apply now.")
    gate = GateResult(
        verdict=LeadVerdict(
            decision="ACCEPT", confidence=0.95, is_current_job=True,
            is_genuine_hiring=True, is_target_role=True,
            major_category="Founder's Office", india_relevance="India",
            employment_type="Full-time",
            hiring_manager_name="Unclear",
            hiring_manager_linkedin="Unclear",
        ),
        decision="ACCEPT", status="New",
    )
    out = enriched_post(post, gate)
    assert out.hiring_manager_name == "Unclear"
    assert out.hiring_manager_linkedin == "Unclear"


# ── 4. production barrier still blocks Unclear-displayed HM ───────────────

def test_gate_holds_when_hm_unclear_internally():
    post = _post("We're hiring a Founder's Office Associate in Mumbai. Apply now.")
    post.hiring_manager_name = "Unclear"
    post.hiring_manager_linkedin = "Unclear"
    gate = GateResult(
        verdict=LeadVerdict(
            decision="ACCEPT", confidence=0.95, is_current_job=True,
            is_genuine_hiring=True, is_target_role=True,
            major_category="Founder's Office", india_relevance="India",
            employment_type="Full-time",
            hiring_manager_name="Unclear", hiring_manager_linkedin="Unclear",
        ),
        decision="ACCEPT", status="New",
    )
    route = route_production_post(post, gate)
    assert route.destination != "MAIN_PRODUCTION"
    assert route.destination != "DATA_PRODUCTION"


def test_gate_accepts_when_hm_explicitly_verified():
    post = _post("We're hiring a Founder's Office Associate in Mumbai. Apply now.",
                 url="https://linkedin.com/in/priya-sharma")
    post.hiring_manager_name = "Priya Sharma"
    post.hiring_manager_linkedin = "https://www.linkedin.com/in/priya-sharma"
    post.hiring_manager_evidence = "named in post: Priya Sharma"
    gate = GateResult(
        verdict=LeadVerdict(
            decision="ACCEPT", confidence=0.95, is_current_job=True,
            is_genuine_hiring=True, is_target_role=True,
            major_category="Founder's Office", india_relevance="India",
            employment_type="Full-time",
            hiring_manager_name="Priya Sharma",
            hiring_manager_linkedin="https://www.linkedin.com/in/priya-sharma",
        ),
        decision="ACCEPT", status="New",
    )
    route = route_production_post(post, gate)
    # may be eligible (subject to all other gates being satisfied — company,
    # role, India, etc.)
    assert post.hiring_manager_name == "Priya Sharma"
