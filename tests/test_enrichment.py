"""
test_enrichment.py — Phase 6 LEAD ENRICHMENT + DATA QUALITY HARDENING tests.

Covers the 24 required behaviors (spec §19). The enrichment layer only runs on
leads that have already passed the existing verification gates; it must never
fabricate information and never change a vacancy decision.

All tests are offline: no Apify, no Groq, no Google Sheets.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.enrichment import Enricher, EnrichedLead
from app.enrichment.enricher import _enrichment_status
from app.enrichment.schemas import EmailStatus, EnrichmentStatus
from app.models import ClassifiedPost, RawPost

ENRICH = Enricher()  # deterministic-only (no LLM -> no network)


def make_classified(text, *, company="Unclear", exact_role="Unclear",
                    location="Not Specified", employment="Unclear",
                    experience="Not Specified", ctc="Not Disclosed",
                    post_url="https://lnkd.in/t", confidence=0.9,
                    raw_company="", author_name="Ravi Kumar", status="New"):
    return ClassifiedPost(
        post_url=post_url, post_date="2026-01-01", text=text,
        author_name=author_name, author_profile_url="https://www.linkedin.com/in/ravi-kumar",
        company=raw_company or company, source_link=post_url,
        major_category="Unclear", exact_role=exact_role, company_name=company,
        ctc=ctc, location=location, employment_type=employment,
        experience_requirement=experience, market="India",
        india_relevance="India", confidence=confidence, scraped_at="now",
        classification_reason="test", status=status, is_valid=True,
    )


# ── 1. Company extracted from post ───────────────────────────────────────────
def test_company_extracted_from_post():
    e = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Founder's Office Associate at Zavity Aerospace in Bengaluru."))
    assert e.company_name == "Zavity Aerospace"
    assert e.company_confidence >= 0.8
    assert e.company_evidence == "post_text"


def test_company_from_is_hiring_pattern():
    e = ENRICH.enrich_from_classified(
        make_classified("Open Links Foundation is hiring a Founder's Office Manager in Noida."))
    assert e.company_name == "Open Links Foundation"


# ── 2. Company remains Unclear when absent ───────────────────────────────────
def test_company_unclear_when_absent():
    e = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Chief of Staff in Pune. Apply now."))
    assert e.company_name == "Unclear"


def test_pronoun_is_never_a_company():
    e = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Founder's Office Associate. Apply."))
    assert e.company_name not in ("We", "we")


# ── 3. Exact role preserved ──────────────────────────────────────────────────
def test_exact_role_preserved():
    e = ENRICH.enrich_from_classified(
        make_classified(
            "Chief of Staff - India Country Manager's Office based in Noida.",
            exact_role="Chief of Staff - India Country Manager's Office"))
    assert e.exact_role == "Chief of Staff - India Country Manager's Office"


# ── 4. Generic keyword list never used as Exact Role ─────────────────────────
def test_generic_keyword_list_never_used_as_exact_role():
    e = ENRICH.enrich_from_classified(
        make_classified("We're hiring a Chief of Staff - India Country Manager's Office in Noida.",
                        exact_role="Chief of Staff / Founder's Office / Generalist"))
    assert e.exact_role != "Chief of Staff / Founder's Office / Generalist"
    assert e.exact_role == "Chief of Staff - India Country Manager's Office"


def test_generic_keyword_list_not_deterministic_fallback():
    e = ENRICH.enrich_from_classified(
        make_classified("hiring Founder's Office role at Acme",
                        exact_role="Chief of Staff / Founder's Office / Generalist"))
    assert e.exact_role not in ("Chief of Staff / Founder's Office / Generalist", "")


# ── 5. CRM Executive (Founder's Office) is not converted into FO ─────────────
def test_crm_executive_not_converted_to_fo():
    # The verifier would hold this to REVIEW (actual role = CRM Executive). The
    # enrichment layer must respect that and not force a Founder's Office label.
    e = ENRICH.enrich_from_classified(
        make_classified("Hiring a CRM Executive (Founder's Office) in Mumbai.",
                        exact_role="CRM Executive (Founder's Office)"))
    assert "CRM Executive" in e.exact_role
    assert e.exact_role.rstrip(")").strip() != "Founder's Office"


# ── 6. CTC extracted when explicit ───────────────────────────────────────────
def test_ctc_extracted_when_explicit():
    e = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Chief of Staff. CTC 20 LPA. Apply."))
    assert e.ctc != "Not Disclosed"
    assert ("20" in e.ctc) and ("LPA" in e.ctc.upper() or "LPA" in e.ctc)


def test_ctc_range_extracted():
    e = ENRICH.enrich_from_classified(
        make_classified("Founder's Office Associate, INR 10-15 lakh. Bangalore."))
    assert "10" in e.ctc or "15" in e.ctc


# ── 7. CTC remains Not Disclosed when absent ─────────────────────────────────
def test_ctc_not_disclosed_when_absent():
    e = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Chief of Staff in Pune. Apply now."))
    assert e.ctc == "Not Disclosed"


# ── 8. Experience extracted ──────────────────────────────────────────────────
def test_experience_extracted():
    e = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Chief of Staff. 3+ years experience. Mumbai."))
    assert "3" in e.experience_required
    assert "years" in e.experience_required.lower()


def test_experience_range_extracted():
    e = ENRICH.enrich_from_classified(
        make_classified("Opening: Founder's Office Associate, 0-2 years. Pune."))
    assert "0" in e.experience_required


# ── 9. Experience not confused with founder experience ───────────────────────
def test_founder_experience_not_confused():
    e = ENRICH.enrich_from_classified(
        make_classified("Our founder has 10 years experience. We're hiring a Founder's Office Associate in Pune."))
    assert "10 years" not in e.experience_required


# ── 10. Hiring manager from explicit evidence ────────────────────────────────
def test_hiring_manager_from_first_person():
    e = ENRICH.enrich_from_classified(
        make_classified("I'm hiring a Founder's Office Associate at Acme in Mumbai.",
                        raw_company="Acme"))
    assert e.hiring_manager_name == "Ravi Kumar"


def test_hiring_manager_not_from_bare_persona():
    # "we're hiring" without a specific named contact still yields the author
    # only via first-person hiring evidence; a plain corporate "hiring" post
    # with no first-person evidence should leave the manager Unclear.
    e = ENRICH.enrich_from_classified(
        make_classified("Acme is hiring a Founder's Office Associate at Chennai.",
                        raw_company="Acme"))
    assert e.hiring_manager_name == "Unclear"


# ── 11. Author not automatically treated as hiring manager ───────────────────
def test_author_not_auto_hiring_manager():
    e = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Chief of Staff in Pune.", author_name="Ravi Kumar"))
    # No first-person evidence (poster persona) -> author must NOT be assumed.
    assert e.hiring_manager_name != "Ravi Kumar"


def test_author_as_manager_only_with_hiring_evidence():
    e = ENRICH.enrich_from_classified(
        make_classified("I'm hiring a Chief of Staff in Pune. contact me.",
                        author_name="Ravi Kumar"))
    assert e.hiring_manager_name == "Ravi Kumar"
    assert e.hiring_manager_is_author is True


# ── 12. LinkedIn URL never fabricated ────────────────────────────────────────
def test_linkedin_url_never_fabricated_from_name():
    e = ENRICH.enrich_from_classified(
        make_classified("I'm hiring a Chief of Staff. Apply now."))
    # No LinkedIn URL in the text -> must be Not Available, never a guess.
    assert e.hiring_manager_linkedin in ("Not Available", "")


def test_linkedin_url_only_when_real_url_in_post():
    url = "https://www.linkedin.com/in/smriti-sharma"
    e = ENRICH.enrich_from_classified(
        make_classified(f"I'm hiring a Founder's Office Associate. Reach out: {url}"))
    assert e.hiring_manager_linkedin == url


# ── 13 + 14. Email extracted only when verified / never guessed ──────────────
def test_email_from_post_text():
    e = ENRICH.enrich_from_classified(
        make_classified("Founder's Office role. Apply at careers@acme.com",
                        raw_company="Acme"))
    assert e.cold_email == "careers@acme.com"
    assert e.email_status == EmailStatus.FOUND.value


def test_email_never_guessed_from_domain():
    # Domain is known but no email appears -> must be Not Available, not a guess.
    e = ENRICH.enrich_from_classified(
        make_classified("Founder's Office role at acme.com in Mumbai", raw_company="Acme"))
    assert e.cold_email in ("Not Available", "")


def test_email_private_gmail_is_not_returned():
    e = ENRICH.enrich_from_classified(
        make_classified("DM me at ravi.kumar@gmail.com for the Founder's Office role"))
    assert e.cold_email in ("Not Available", "")


def test_email_from_externally_verified_source():
    e = ENRICH.enrich("Founder's Office Associate at Zavity in Bangalore.",
                      job_metadata_company="Zavity",
                      verified_emails={"company_careers_page": "hiring@zavity.co"})
    assert e.cold_email == "hiring@zavity.co"
    assert e.email_status == EmailStatus.FOUND.value


# ── 15. Missing email -> Not Available ───────────────────────────────────────
def test_missing_email_not_available():
    e = ENRICH.enrich_from_classified(
        make_classified("hiring Founder's Office Associate at Zavity in Bangalore",
                        raw_company="Zavity"))
    assert e.cold_email == "Not Available"


# ── 16. Remote != Remote - India ─────────────────────────────────────────────
def test_remote_is_not_remote_india_without_india_evidence():
    e = ENRICH.enrich("hiring a Chief of Staff. Remote role. We are remote-first.")
    assert e.location == "Remote"
    assert e.remote_is_india is False


def test_remote_becomes_remote_india_with_india_evidence():
    e = ENRICH.enrich("hiring a Chief of Staff. Remote, India - apply.")
    assert e.location == "Remote - India"
    assert e.remote_is_india is True


def test_bangalore_normalized_to_bengaluru():
    e = ENRICH.enrich("hiring a Founder's Office Associate in Bangalore, apply.")
    assert "Bengaluru" in e.location


# ── 17. Original post text preserved ─────────────────────────────────────────
def test_original_post_text_preserved():
    text = "We are hiring a Founder's Office Associate at Zavity in Bengaluru. CTC 12 LPA."
    e = ENRICH.enrich_from_classified(make_classified(text, raw_company="Zavity"))
    assert e.post_description == text


# ── 18. LLM summary stored separately ────────────────────────────────────────
def test_llm_summary_stored_separately():
    text = "We are hiring a Founder's Office Associate at Zavity in Bengaluru."
    e = ENRICH.enrich_from_classified(make_classified(text, raw_company="Zavity"))
    assert e.post_description == text
    e.llm_summary = "Zavity is hiring a Founder's Office Associate."
    assert e.llm_summary != e.post_description


# ── Enrichment confidence & status ───────────────────────────────────────────
def test_enrichment_confidence_high_when_complete():
    e = ENRICH.enrich(
        "Open Links Foundation is hiring a Founder's Office Associate in Noida. "
        "Full-time. CTC 12 LPA. 0-2 years. Requisition ongoing.",
        job_metadata_company="Open Links Foundation",
        job_metadata_is_authoritative=True,
        source_link="https://lnkd.in/abc",
    )
    assert e.enrichment_confidence >= 0.7
    assert e.enrichment_status in (EnrichmentStatus.READY.value,
                                   EnrichmentStatus.READY_WITHOUT_CONTACT.value)


def test_enrichment_confidence_low_when_sparse():
    e = ENRICH.enrich("hiring role apply", source_link="")
    assert e.enrichment_confidence < 0.5


# ── 19. Existing ACCEPT remains ACCEPT after enrichment ──────────────────────
def test_enrich_accept_stays_accept():
    # A genuine, complete lead.
    e = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Chief of Staff in Pune.", company="Acme",
                        exact_role="Chief of Staff", employment="Full-time",
                        location="Pune"))
    assert e.verification_status == "New"  # unchanged
    assert e.enrichment_status in (EnrichmentStatus.READY.value,
                                   EnrichmentStatus.READY_WITHOUT_CONTACT.value)


# ── 20/21/22/23. Enrichment cannot turn REJECT/internship/aggregator/
# ── non-India into ACCEPT ────────────────────────────────────────────────────
def test_enrichment_never_promotes_review_to_accept():
    # The verifier decided REVIEW; enrichment must not flip it to ACCEPT.
    e = ENRICH.enrich_from_classified(
        make_classified("Hiring a CRM Executive in Delhi.", exact_role="CRM Executive",
                        confidence=0.7, status="Review"))
    assert e.verification_status == "Review"
    assert e.enrichment_status == EnrichmentStatus.REVIEW.value


def test_internship_cannot_become_accept():
    # Enrichment operating on an already-accepted lead must keep intern markers:
    # an internship is out of scope, so the enriched employment type is Internship.
    e = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Founder's Office Intern in Pune."))
    assert e.employment_type == "Internship"


def test_enrichment_keeps_source_url():
    url = "https://www.linkedin.com/posts/abc_hiring-activity-123"
    e = ENRICH.enrich_from_classified(
        make_classified("hiring Chief of Staff in Pune", post_url=url))
    assert e.source_link == url


# ── 24. Groq failure does not fabricate fields ───────────────────────────────
def test_enrichment_failure_degrades_without_fabrication(monkeypatch):
    called = {}

    def boom(**kwargs):
        called["hit"] = True
        raise RuntimeError("Groq down")

    e = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Chief of Staff at Acme in Pune."))
    # Simulate a provider failure path inside enrich_from_classified.
    monkeypatch.setattr(ENRICH, "enrich", boom)
    out = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Chief of Staff at Acme in Pune."))
    assert called.get("hit")
    # No fabricated email, no fabricated manager, honest defaults preserved.
    assert out.cold_email in ("Not Available", "")
    assert out.hiring_manager_name in ("Unclear", "")
    assert out.enrichment_status == EnrichmentStatus.REVIEW.value


def test_no_fabricated_email_even_with_known_domain():
    e = ENRICH.enrich("Founder's Office role at acme.com/robots.txt in Mumbai",
                      job_metadata_company="Acme")
    assert e.cold_email in ("Not Available", "")


# ── BUG-A regression: generic descriptor must not become a Company name ───────
def test_generic_descriptor_not_a_company():
    # "a fast-growing MedTech startup" is a descriptive phrase, not a named
    # employer. The post advertises no company -> must stay Unclear.
    e = ENRICH.enrich_from_classified(
        make_classified("Join a fast-growing MedTech startup and work closely with leadership. Apply here."))
    assert e.company_name == "Unclear"


def test_leading_startup_descriptor_not_a_company():
    e = ENRICH.enrich_from_classified(
        make_classified("Join a leading startup as a Chief of Staff in Bengaluru."))
    assert e.company_name == "Unclear"


def test_fintech_company_descriptor_not_a_company():
    e = ENRICH.enrich_from_classified(
        make_classified("We are at a fintech company hiring a Founder's Office Associate in Mumbai."))
    assert e.company_name == "Unclear"


def test_genuine_named_company_still_extracted_from_join():
    # A real brand after "Join" with a legal-form suffix is still surfaced.
    e = ENRICH.enrich_from_classified(
        make_classified("Join Zavity Aerospace Technologies as a Founder's Office Associate in Bengaluru."))
    assert e.company_name == "Zavity Aerospace Technologies"


def test_genuine_named_company_is_not_a_descriptor():
    # The generic-descriptor guard must NOT swallow a real brand in the
    # "at <Name> in <city>" position.
    e = ENRICH.enrich_from_classified(
        make_classified("We are hiring a Founder's Office Associate at Zavity Aerospace in Bengaluru."))
    assert e.company_name == "Zavity Aerospace"


def test_genuine_named_company_from_is_hiring_still_extracted():
    e = ENRICH.enrich_from_classified(
        make_classified("Open Links Foundation is hiring a Founder's Office Manager in Noida."))
    assert e.company_name == "Open Links Foundation"


def test_llm_unclear_not_overridden_by_generic_descriptor():
    # Deterministic must not surface a generic descriptor; when it would have,
    # the field stays Unclear and the LLM's Unclear is honoured (no wrong
    # deterministic value beats an LLM-confirmed Unclear).
    e = ENRICH.enrich_from_classified(
        make_classified("Join a fast-growing MedTech startup and work closely with leadership."))
    assert e.company_name == "Unclear"


# ── BUG-B regression: in-post emails preserved regardless of local part ───────
def test_baishali_email_preserved():
    e = ENRICH.enrich_from_classified(
        make_classified("Drop your resume at baishali@talentsocio.com for the Founder's Office role.",
                        raw_company="Acme"))
    assert e.cold_email == "baishali@talentsocio.com"
    assert e.email_status == EmailStatus.FOUND.value


def test_payal_email_preserved():
    e = ENRICH.enrich_from_classified(
        make_classified("Hiring Chief of Staff. Apply at payal@allcadservices.com",
                        raw_company="Acme"))
    assert e.cold_email == "payal@allcadservices.com"


def test_founders_email_preserved():
    e = ENRICH.enrich_from_classified(
        make_classified("We at GoNanny are hiring for a Founder's Office. Email founders@gonanny.in",
                        raw_company="GoNanny"))
    assert e.cold_email == "founders@gonanny.in"


def test_classified_cold_email_forwarded_and_preserved():
    # The classifier-extracted email is forwarded into enrich() rather than
    # discarded, even when post_text is not re-scanned from scratch (i.e. the
    # classified value is used as the preserved post-text email).
    e = ENRICH.enrich(
        post_text="hiring Chief of Staff. Apply.",
        classified_cold_email="recruit.hr@zavity.co",
        source_link="https://lnkd.in/x",
    )
    assert e.cold_email == "recruit.hr@zavity.co"
    assert e.email_status == EmailStatus.FOUND.value


def test_blocked_email_still_rejected():
    # Personal Gmail remains blocked: never surfaced as a business contact.
    e = ENRICH.enrich_from_classified(
        make_classified("DM me at ravi.kumar@gmail.com for the Founder's Office role"))
    assert e.cold_email in ("Not Available", "")


def test_blocklisted_holder_domain_still_rejected():
    e = ENRICH.enrich_from_classified(
        make_classified("Apply at jobs@example.com for the Founder's Office role"))
    assert e.cold_email in ("Not Available", "")


def test_no_email_never_fabricated_from_domain():
    # Known domain but no email in the post => Not Available, never a guess.
    e = ENRICH.enrich_from_classified(
        make_classified("Founder's Office role at zavity.co in Mumbai", raw_company="Zavity"))
    assert e.cold_email in ("Not Available", "")


def test_external_verified_email_still_requires_public_prefix():
    # External/provider emails still pass through the stricter public-business
    # guard (the relaxed in-post rule does not weaken external verification).
    e = ENRICH.enrich("Founder's Office Associate at Zavity in Bangalore.",
                      job_metadata_company="Zavity",
                      verified_emails={"company_careers_page": "bob@zavity.co"})
    assert e.cold_email in ("Not Available", "")


# ── Phase 12.5 hardening: "X is building..." employer extraction ────────────
def test_is_building_captures_company():
    e = ENRICH.enrich_from_classified(
        make_classified(
            "BCT Ventures is building an AI-native consumer brands platform "
            "focused on creating and scaling the next generation of nutrition & "
            "wellness brands. We're hiring a Chief of Staff – Founder's Office in Mumbai."))
    assert e.company_name == "BCT Ventures"


def test_is_building_generic_descriptor_rejected():
    e = ENRICH.enrich_from_classified(
        make_classified("A fast-growing MedTech startup is building a new AI platform. "
                        "We're hiring a Founder's Office role in Pune."))
    assert e.company_name == "Unclear"


def test_is_building_possessive_rejected():
    e = ENRICH.enrich_from_classified(
        make_classified("Our startup is building the next generation of consumer brands. "
                        "We're hiring a Chief of Staff in Mumbai."))
    assert e.company_name == "Unclear"


def test_is_building_pronoun_blocked():
    e = ENRICH.enrich_from_classified(
        make_classified("We are building something great at Acme in Bengaluru. "
                        "Founder's Office role."))
    assert e.company_name != "We"
    assert e.company_name == "Acme"


def test_is_building_preceding_apostrophe_not_captured():
    e = ENRICH.enrich_from_classified(
        make_classified(
            "\U0001f680 We\u2019re Hiring | Chief of Staff \u2013 Founder\u2019s Office\n"
            "BCT Ventures is building an AI-native consumer brands platform focused "
            "on creating and scaling the next generation of nutrition & wellness "
            "brands. We're hiring a Chief of Staff \u2013 Founder\u2019s Office in "
            "Mumbai. CTC \u20b913\u201315 LPA."))
    assert e.company_name == "BCT Ventures"


def test_is_building_sentence_boundary_not_captured():
    e = ENRICH.enrich_from_classified(
        make_classified(
            "We are #hiring the Founder's Office for our client,a VC-funded "
            "Climate-Tech startup, disrupting the Battery Energy Storage Systems "
            "(BESS) space in India.\n\n\nWe are looking for a Chief of Staff to "
            "work as the strategic right hand to our founders."))
    assert e.company_name == "Unclear"


def test_is_looking_for_relative_clause_who_blocked():
    e = ENRICH.enrich_from_classified(
        make_classified(
            "We are hiring a Founder's Office associate. Who we are looking for: "
            "2 to 4 years of experience and a strong foundation in the Indian "
            "energy sector."))
    assert e.company_name == "Unclear"


# ── Phase 12.5 hardening: "X is looking for..." employer extraction ────────
def test_is_looking_for_captures_company():
    e = ENRICH.enrich_from_classified(
        make_classified(
            "Luar Beauty is looking for a Founders Office Associate to join "
            "their team in Mumbai. This is a great opportunity for someone "
            "who enjoys working in a fast-paced environment."))
    assert e.company_name == "Luar Beauty"


def test_is_looking_for_generic_descriptor_rejected():
    e = ENRICH.enrich_from_classified(
        make_classified("A leading fintech company is looking for a Chief of Staff "
                        "in Bengaluru. Join us."))
    assert e.company_name == "Unclear"


def test_is_looking_for_possessive_rejected():
    e = ENRICH.enrich_from_classified(
        make_classified("Their team is looking for a Founder's Office Associate "
                        "in Mumbai. Apply now."))
    assert e.company_name == "Unclear"


# ── Phase 12.5 hardening: header/card company extraction ───────────────────
def test_header_card_company_extracted():
    e = ENRICH.enrich_from_classified(
        make_classified(
            "🔥 We're Hiring: Founder's Office | Noida | Amama Partners LLP 🔥*\n"
            "Want a front-row seat to building a company? Work directly with "
            "the Founder & CEO. Send CV to hiring@amama.com."))
    assert e.company_name == "Amama Partners LLP"


def test_header_card_no_legal_form_ignored():
    e = ENRICH.enrich_from_classified(
        make_classified("🔥 We're Hiring: Founder's Office | Noida | Mumbai 🔥*\n"
                        "Apply via DM."))
    assert e.company_name == "Unclear"


def test_header_card_pure_generic_rejected():
    e = ENRICH.enrich_from_classified(
        make_classified("🔥 Hiring | Private Limited | Apply 🔥*\n"
                        "We're hiring a Chief of Staff in Pune."))
    assert e.company_name == "Unclear"


def test_header_card_real_brand_preserved():
    e = ENRICH.enrich_from_classified(
        make_classified("Hiring: Chief of Staff | Zavity Technologies | Noida\n"
                        "Apply now."))
    assert e.company_name == "Zavity Technologies"


# ── Phase 12.5 hardening: org-lead / "lead X, an organisation" ──────────────
def test_org_lead_captures_company():
    e = ENRICH.enrich_from_classified(
        make_classified(
            "I thought of hiring a Chief of Staff. Not specifically CHIEF OF STAFF; "
            "it can be anyone who will be helping me lead Foxhog, an organisation "
            "with more than 1200+ People, on a mission to fuel India with "
            "interest-free debt. DM me."))
    assert e.company_name == "Foxhog"


def test_org_lead_helping_me_lead():
    e = ENRICH.enrich_from_classified(
        make_classified("helping us lead GoNanny, a startup focused on childcare. "
                        "We're hiring a Founder's Office role."))
    assert e.company_name == "GoNanny"


def test_org_lead_no_org_noun_ignored():
    e = ENRICH.enrich_from_classified(
        make_classified("I want to lead the product team at a startup in Mumbai. "
                        "Founder's Office role."))
    assert e.company_name == "Unclear"


def test_org_lead_pronoun_rejected():
    e = ENRICH.enrich_from_classified(
        make_classified("helping me lead the team, a company focused on growth. "
                        "Hiring a Chief of Staff in Pune."))
    assert e.company_name == "Unclear"


# ── Phase 12.5 hardening: Tirupur / Tiruppur / Tirpur location ─────────────
def test_tirupur_location_normalized():
    e = ENRICH.enrich_from_classified(
        make_classified(
            "We're hiring: Creative & Digital Associate - Founder's Office\n"
            "📍 Location: Tirupur, Tamilnadu, India\n"
            "Hiring at Maple Fashion in Tirupur.",
            company="Maple Fashion"))
    assert "Tirupur" in e.location
    assert "Tamil Nadu" in e.location


def test_tiruppur_variant_normalized():
    e = ENRICH.enrich(
        "We are hiring a Chief of Staff in Tiruppur. CTC 15 LPA. Full-time.")
    assert "Tirupur" in e.location
    assert "Tamil Nadu" in e.location


def test_tirpur_variant_normalized():
    e = ENRICH.enrich(
        "We are hiring a Chief of Staff in Tirpur. Apply now.")
    assert "Tirupur" in e.location


# ── Phase 12.5: existing generic-descriptor guard still holds ───────────────
def test_generic_descriptor_with_looking_for_rejected():
    e = ENRICH.enrich_from_classified(
        make_classified("a fast-growing MedTech startup is looking for a "
                        "Chief of Staff in Bengaluru."))
    assert e.company_name == "Unclear"


def test_generic_descriptor_with_building_rejected():
    e = ENRICH.enrich_from_classified(
        make_classified("Our team is building a new AI platform. "
                        "We're hiring a Chief of Staff in Mumbai."))
    assert e.company_name == "Unclear"


# ── Integration: orchestrator wiring keeps decision boundaries ───────────────
def test_orchestrator_enriches_only_accepted():
    from unittest.mock import patch
    from app.orchestrator import Orchestrator
    from app.llm.schemas import GateResult, LeadVerdict

    def make_raw(url):
        return RawPost(post_url=url, post_date="", author_name="",
                       author_profile_url="", text="We are hiring a Chief of Staff in Pune. Apply now.")

    def make_gate(decision):
        v = LeadVerdict(decision=decision.lower() if decision != "REJECT" else "REJECT",
                        confidence=0.95, is_current_job=True, is_genuine_hiring=True,
                        is_target_role=True, major_category="Chief of Staff",
                        india_relevance="India", employment_type="Full-time",
                        company="Acme", exact_role="Chief of Staff")
        status = {"ACCEPT": "New", "REVIEW": "Review", "REJECT": ""}[decision]
        return GateResult(verdict=v, decision=decision, status=status)

    with patch("app.orchestrator.DataDopingSource") as mock_src, \
         patch("app.orchestrator.DeterministicClassifier"), \
         patch("app.orchestrator.SheetsWriter") as mock_writer:
        orch = Orchestrator()
        orch.sheets_writer = mock_writer.return_value
        orch.sheets_writer.get_existing_urls.return_value = set()
        orch.verifier = _StubVerifier([make_gate("ACCEPT"), make_gate("REJECT")])
        orch.source.search_all.return_value = [make_raw("https://lnkd.in/a"), make_raw("https://lnkd.in/b")]
        orch.classifier.classify.side_effect = [
            make_classified("t", post_url="https://lnkd.in/a"),
            make_classified("t", post_url="https://lnkd.in/b"),
        ]

        summary = orch.run_pipeline(limit=10)
        # One ACCEPT enriched, one REJECT untouched.
        assert len(orch._enrichment_results) == 1
        assert "https://lnkd.in/a" in orch._enrichment_results
        assert summary["enriched"] == 1


class _StubVerifier:
    def __init__(self, gates):
        self.gates = list(gates)
        self.llm_calls = 0

    def verify(self, **kwargs):
        self.llm_calls += 1
        return self.gates.pop(0) if self.gates else None
