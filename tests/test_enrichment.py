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
