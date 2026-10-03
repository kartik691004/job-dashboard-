"""
test_phase18_llm_recovery.py — Phase 18 LLM status semantics, recovery path,
and Unclear-resolution hardening. Fully offline (scripted providers only).

Frozen fixtures come from real audited posts (Phase 18J/K universe) or minimal
faithful shapes. The frozen baseline (610 passed / 1 skipped) must stay green:
no deterministic gate is widened here — every previously-rejected shape still
rejects, and every previously-valid shape keeps its verdict.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier import DeterministicClassifier
from app.llm.recovery import (classify_old_record, recover_record,
                              run_recovery)
from app.llm.schemas import GateResult, LeadVerdict
from app.llm.verifier import SYSTEM_PROMPT, Verifier, _sanitize_error
from app.models import RawPost
from app.resolution import (EmploymentState, EmployerState, HMState,
                            ResolutionState, audit_post_fields,
                            employer_state_of, field_confidence, hm_state_of,
                            resolve_company, resolve_employment, resolve_hm,
                            resolve_location, resolve_role)

CLF = DeterministicClassifier()


def make_post(text, author="Aditi Rao",
              profile="https://linkedin.com/in/aditi-rao"):
    return RawPost(
        post_url="https://www.linkedin.com/posts/test-activity-1",
        post_date="2026-09-01",
        text=text,
        author_name=author,
        author_profile_url=profile,
    )


class ScriptedProvider:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        if self.error is not None:
            from app.llm.base import LLMError
            raise LLMError(self.error)
        return dict(self.payload)


# Recovery requires an explicit GroqProvider; tests fake the class name the
# same way (production guard untouched — see test_recovery_rejects_non_groq).
ScriptedProvider.__name__ = "GroqProvider"


def accept_payload(**over):
    base = {"decision": "ACCEPT", "confidence": 0.95,
            "is_current_job": True, "is_genuine_hiring": True,
            "is_target_role": True, "major_category": "Data Analyst",
            "actual_role": "Data Analyst", "exact_role": "Data Analyst",
            "company": "Acme", "india_relevance": "India",
            "location": "Mumbai", "employment_type": "Full-time",
            "reason": "genuine"}
    base.update(over)
    return base


# ── 18A: status semantics ───────────────────────────────────────────────────

def test_disabled_verifier_is_not_run_not_zero():
    gate = Verifier(None).verify(post_text="We are hiring a Data Analyst in Mumbai.")
    assert gate.decision == "REVIEW" and gate.llm_called is False
    assert gate.llm_status == "NOT_RUN"
    assert gate.llm_confidence is None  # NOT 0.00-as-evaluation
    assert gate.confidence == 0.0  # backward-compatible sheet path untouched


def test_transport_failure_is_unavailable_not_rejection():
    gate = Verifier(ScriptedProvider(error="Groq HTTP 429 rate limit exceeded after retries")).verify(
        post_text="We are hiring a Data Analyst in Mumbai. Full-time.")
    assert gate.decision == "REVIEW"  # never a rejection
    assert gate.llm_status == "UNAVAILABLE"
    assert gate.llm_confidence is None
    assert gate.llm_provider == "GroqProvider"  # faked class name (see above)
    assert "429" in gate.llm_error


def test_valid_rejection_carries_status_and_confidence():
    gate = Verifier(ScriptedProvider(accept_payload(
        decision="REJECT", is_target_role=False, major_category="None",
        actual_role="Software Engineer", confidence=0.96))).verify(post_text="x")
    assert gate.decision == "REJECT"
    assert gate.llm_status == "REJECT"
    assert gate.llm_confidence == 0.96  # populated, not null


def test_accept_maps_to_success_with_confidence():
    gate = Verifier(ScriptedProvider(accept_payload())).verify(
        post_text="We are hiring a Data Analyst in Mumbai. Full-time.")
    assert gate.decision == "ACCEPT" and gate.llm_status == "SUCCESS"
    assert gate.llm_confidence == 0.95


def test_review_maps_to_review_status():
    gate = Verifier(ScriptedProvider(accept_payload(confidence=0.85))).verify(
        post_text="We are hiring a Data Analyst in Mumbai. Full-time.")
    assert gate.decision == "REVIEW" and gate.llm_status == "REVIEW"
    assert gate.llm_confidence == 0.85


def test_error_text_sanitizer_redacts_secrets():
    dirty = "Groq HTTP 400 Bearer gsk_abc123XYZ api_key=secret sk-live-1 AQ.Ab8XYZ Authorization: token"
    clean = _sanitize_error(dirty)
    assert "gsk_abc123XYZ" not in clean and "sk-live-1" not in clean
    assert "AQ.Ab8XYZ" not in clean and "secret" not in clean
    assert "Groq HTTP 400" in clean


def test_hand_built_gate_defaults_to_success():
    g = GateResult(verdict=LeadVerdict(), decision="REVIEW", status="Review")
    assert g.llm_status == "SUCCESS" and g.llm_confidence == 0.0


# ── 18L fixtures 1-3: explicit vs undisclosed companies ─────────────────────

ONEXTAL_TEXT = (
    "Onextal is HIRING | CHIEF OF STAFF \u2013 CEO OFFICE \U0001f4cd Noida | "
    "Reporting directly to the CEO. 2\u20135 years. Full-time. Apply now."
)

KARTA_TEXT = (
    "#JobOpening | Karta Initiative India Foundation is Hiring! Looking to "
    "build your career across leadership and programmes. Karta Initiative "
    "India Foundation is hiring for multiple roles in Noida. Explore these "
    "opportunities: Founder\u2019s/CEO\u2019s Office or Chief of Staff "
    "https://lnkd.in/dsnzu5bX Fellow \u2013 Educator https://lnkd.in/edu123"
)


def test_onextel_explicit_company_resolves():
    r = CLF.classify(make_post(ONEXTAL_TEXT))
    assert r.is_valid
    assert r.company_name == "Onextal"
    res = resolve_company(ONEXTAL_TEXT)
    assert res.value == "Onextal"
    assert employer_state_of(res) == EmployerState.EXPLICIT_EMPLOYER
    assert "HIRING" in res.evidence


def test_karta_explicit_company_resolves():
    r = CLF.classify(make_post(KARTA_TEXT))
    assert r.is_valid
    assert r.company_name == "Karta Initiative India Foundation"
    res = resolve_company(KARTA_TEXT)
    assert employer_state_of(res) == EmployerState.EXPLICIT_EMPLOYER


def test_karta_role_not_url_fused():
    r = CLF.classify(make_post(KARTA_TEXT))
    assert "http" not in r.exact_role and "lnkd.in" not in r.exact_role
    assert "Fellow" not in r.exact_role
    assert r.exact_role == "Chief of Staff"


UNDISCLOSED_TEXT = (
    "JOB HIRING: DATA SCIENTIST. Company: Not to disclose as per our "
    "agreement with client. Location: Pune. Type: FULL_TIME. Experience: "
    "3-5 Years. Apply here: https://lnkd.in/abc123XYZ"
)


def test_undisclosed_client_stays_unclear_with_state():
    r = CLF.classify(make_post(UNDISCLOSED_TEXT))
    assert r.is_valid  # vacancy itself is genuine; attribution is the gap
    assert r.company_name == "Unclear"
    res = resolve_company(UNDISCLOSED_TEXT)
    assert res.value == "Unclear"
    assert employer_state_of(res) == EmployerState.INTERMEDIARY_CLIENT_UNDISCLOSED


def test_stealth_startup_is_employer_undisclosed():
    res = resolve_company(
        "We are hiring a Data Analyst in Mumbai. Stealth startup, company "
        "confidential for now. Full-time. Apply now.")
    assert employer_state_of(res) == EmployerState.EMPLOYER_UNDISCLOSED


def test_no_evidence_is_extraction_unresolved():
    res = resolve_company("We are hiring a Data Analyst in Mumbai. Full-time.")
    assert employer_state_of(res) == EmployerState.EXTRACTION_UNRESOLVED


# ── 18L fixtures 4-5: HM attribution ────────────────────────────────────────

def test_company_page_poster_never_becomes_hm():
    r = CLF.classify(make_post(
        "We're hiring a Data Analyst in Mumbai. Full-time. Apply now.",
        author="Acme Talent", profile="https://www.linkedin.com/company/acme"))
    assert r.is_valid
    assert r.hiring_manager_name == "Unclear"
    res = resolve_hm(r.hiring_manager_name, r.hiring_manager_evidence,
                     "Acme Talent", "https://www.linkedin.com/company/acme",
                     r.description)
    assert hm_state_of(res) == HMState.POSTER_ONLY


def test_im_hiring_is_direct_hiring_person():
    r = CLF.classify(make_post(
        "I'm hiring a Data Analyst in Mumbai. Full-time. DM me."))
    assert r.hiring_manager_name == "Aditi Rao"
    res = resolve_hm(r.hiring_manager_name, r.hiring_manager_evidence,
                     "Aditi Rao", "https://linkedin.com/in/aditi-rao",
                     r.description)
    assert hm_state_of(res) == HMState.DIRECT_HIRING_PERSON


def test_named_cofounder_is_direct_hiring_person():
    r = CLF.classify(make_post(
        "We are hiring a Data Analyst in Delhi. You will work with me and my "
        "co-founder Rishi Jain. Full-time."))
    assert r.hiring_manager_name == "Rishi Jain"
    res = resolve_hm(r.hiring_manager_name, r.hiring_manager_evidence,
                     "Someone", "https://linkedin.com/in/someone", r.description)
    assert hm_state_of(res) == HMState.DIRECT_HIRING_PERSON


def test_bare_name_without_evidence_is_ambiguous():
    res = resolve_hm("Rahul Sharma", "", "Poster", "https://linkedin.com/in/x", "")
    assert hm_state_of(res) == HMState.AMBIGUOUS


# ── 18L fixtures 8-10: role & location preservation ─────────────────────────

def test_senior_data_scientist_preserved():
    r = CLF.classify(make_post(
        "FourKites is hiring a Remote Senior Data Scientist. Full-time, "
        "Remote India. Apply now: https://lnkd.in/x"))
    assert r.is_valid
    assert r.exact_role == "Senior Data Scientist"


def test_cos_ceo_office_specialization_preserved():
    r = CLF.classify(make_post(ONEXTAL_TEXT))
    assert r.exact_role == "CHIEF OF STAFF \u2013 CEO OFFICE"


def test_remote_india_location_preserved():
    r = CLF.classify(make_post(
        "FourKites is hiring a Remote Senior Data Scientist. Full-time, "
        "Remote India. Apply now: https://lnkd.in/x"))
    assert r.location == "Remote" or "India" in (r.india_relevance or "")
    res = resolve_location(r.location, r.india_evidence, "")
    assert res.resolution in (ResolutionState.EXPLICIT, ResolutionState.EXTRACTED)


def test_role_punctuation_shapes():
    cases = [
        # Parenthetical specialization is preserved verbatim (18E).
        ("Hiring Chief of Staff (CEO Office) in Mumbai. Full-time. Apply now.",
         "Chief of Staff (CEO Office)"),
        ("Role: Data Scientist - Gen AI in Pune. Full-time. Apply now.",
         "Data Scientist - Gen AI"),
        ("We are hiring a Founder\u2019s Office Associate in Mumbai. Full-time.",
         "Founder\u2019s Office Associate"),
    ]
    for text, expected in cases:
        r = CLF.classify(make_post(text))
        assert r.is_valid, text
        assert r.exact_role == expected, (text, r.exact_role)


# ── 18L fixtures 11-13: employment unknown, scam, aggregator ────────────────

def test_unknown_employment_is_not_rejection():
    r = CLF.classify(make_post(
        "We are hiring a Data Analyst in Mumbai. Apply now."))
    assert r.is_valid
    res = resolve_employment(r.employment_type)
    assert res.value == "Unclear"
    assert EmploymentState.from_string(r.employment_type) == EmploymentState.UNKNOWN


def test_employment_states_map():
    assert EmploymentState.from_string("Full-time") == EmploymentState.FULL_TIME
    assert EmploymentState.from_string("Internship") == EmploymentState.INTERNSHIP
    assert EmploymentState.from_string("Contract") == EmploymentState.CONTRACT
    assert EmploymentState.from_string("") == EmploymentState.UNKNOWN


def test_pay_to_apply_still_rejected():
    r = CLF.classify(make_post(
        "Hiring Data Analyst in Mumbai. Application fee Rs.500 applies. "
        "Full-time. Apply now."))
    assert not r.is_valid
    assert "Pay-to-apply" in r.classification_reason


def test_security_deposit_still_rejected():
    r = CLF.classify(make_post(
        "We are hiring a Chief of Staff in Delhi. Security deposit Rs.10000 "
        "required. Apply now."))
    assert not r.is_valid


def test_aggregator_roundup_still_rejected():
    r = CLF.classify(make_post(
        "MASS HIRING | BATCH: 2022-2028. 1. Company: Stripe. Role: Data "
        "Analyst, Bangalore. 2. Company: Acme. Role: Data Scientist, Mumbai. "
        "Follow for daily job updates."))
    assert not r.is_valid
    assert "ggregator" in r.classification_reason


# ── Old-record classification (18J input triage) ────────────────────────────

def test_gemini_429_row_maps_to_unavailable():
    old = classify_old_record({
        "Company Name": "Sagepilot AI", "Confidence Score": "0",
        "Status": "Review",
        "Classification Reason": "REVIEW: LLM verification unavailable "
        "(provider error: Gemini HTTP 429 rate limit exceeded after retries)"})
    assert old["llm_status"] == "UNAVAILABLE"
    assert old["provider"] == "gemini"
    assert old["confidence"] == 0.0


def test_groq_400_row_maps_to_unavailable():
    old = classify_old_record({
        "confidence": 0.0, "status": "Review",
        "reason": "REVIEW: LLM verification unavailable (provider error: "
                  "Groq call failed after 3 attempt(s): Groq HTTP 400)"})
    assert old["llm_status"] == "UNAVAILABLE"
    assert old["provider"] == "groq"


def test_deterministic_zero_is_not_an_llm_failure():
    old = classify_old_record({
        "source_linkedin_url": "https://x", "company": "Unclear",
        "confidence_score": "0.0", "verification_status": "REJECT",
        "rejection_review_reason": "No relevant role keyword found"})
    assert old["llm_status"] == "NOT_RUN"
    assert "deterministic" in old["note"]


def test_evaluated_accept_maps_to_success():
    old = classify_old_record({"decision": "ACCEPT", "confidence": 0.95,
                               "llm_called": True})
    assert old["llm_status"] == "SUCCESS" and old["confidence"] == 0.95


# ── Recovery path unit behavior (no network) ────────────────────────────────

def test_recovery_rejects_non_groq_verifier():
    with pytest.raises(ValueError, match="explicit GroqProvider"):
        recover_record({"source_url": "https://x", "text": "t",
                        "old": {}, "old_stored": {}},
                       Verifier(None))


def test_recovery_makes_no_llm_call_when_deterministic_rejects():
    class ExplodingProvider:
        def __init__(self):
            self.calls = 0
            self.model = "groq-exploding"

        def complete_json(self, system, user):
            self.calls += 1
            raise AssertionError("must not be called")

    # Fake the class name so the Groq-only guard passes without network.
    ExplodingProvider.__name__ = "GroqProvider"
    import app.llm.recovery as rec_mod
    real = rec_mod._require_groq_verifier
    try:
        rec_mod._require_groq_verifier = lambda v: "GroqProvider"
        out = recover_record(
            {"source_url": "https://x",
             "text": "Open to work as a Data Analyst in Mumbai.",
             "old": classify_old_record({"confidence": 0.0, "decision": "REVIEW"}),
             "old_stored": {}},
            Verifier(ScriptedProvider(accept_payload())), None)
    finally:
        rec_mod._require_groq_verifier = real
    assert out["groq_calls"] == 0
    assert out["disposition"] == "REJECT"
    assert "deterministic gate unchanged" in out["disposition_reason"]


def test_recovery_hold_without_text():
    out = recover_record({"source_url": "https://x", "text": "   ",
                          "old": {}, "old_stored": {}},
                         Verifier(ScriptedProvider(accept_payload())), None)
    assert out["disposition"] == "HOLD"


def test_derived_accept_caps_at_review():
    from app.enrichment import Enricher
    out = recover_record(
        {"source_url": "https://x",
         "text": "We are hiring a Data Analyst in Mumbai. Full-time. Apply now.",
         "input_fidelity": "DESCRIPTION_DERIVED",
         "old": {"llm_status": "UNAVAILABLE"},
         "old_stored": {"company": "Unclear"}},
        Verifier(ScriptedProvider(accept_payload())), Enricher())
    assert out["disposition"] == "REVIEW"
    assert "derived" in out["disposition_reason"]


def test_exact_accept_recovers_with_diff():
    from app.enrichment import Enricher
    out = recover_record(
        {"source_url": "https://x",
         "text": "Onextal is HIRING for a Data Analyst in Noida. Full-time. "
                 "Apply now.",
         "input_fidelity": "EXACT",
         "old": {"llm_status": "UNAVAILABLE", "confidence": 0.0},
         "old_stored": {"company": "Unclear", "role": "Unclear"}},
        Verifier(ScriptedProvider(accept_payload(company="Onextal"))),
        Enricher())
    assert out["disposition"] == "ACCEPT"
    assert out["groq_calls"] == 1
    assert out["recovery"]["llm_status"] == "SUCCESS"
    assert out["recovery"]["llm_confidence"] == 0.95
    fields = {c["field"]: c for c in out["changed_fields"]}
    assert fields["company"]["old"] == "Unclear"
    assert fields["company"]["new"] == "Onextal"
    assert out["enrichment"] is not None


# ── Resolution audit + field confidence (18D/18I) ───────────────────────────

def test_audit_post_fields_labels_everything():
    cp = CLF.classify(make_post(
        "Onextal is HIRING for a Data Analyst in Noida. Full-time. 2-4 years. "
        "Apply now."))
    assert cp.is_valid
    audit = audit_post_fields(cp)
    assert set(audit) >= {"company", "role", "location", "employment_type",
                          "experience", "hiring_manager"}
    assert audit["company"]["employer_state"] == EmployerState.EXPLICIT_EMPLOYER
    assert audit["company"]["confidence"] == 0.95
    assert audit["employment_type"]["employment_state"] == EmploymentState.FULL_TIME
    for key, val in audit.items():
        assert 0.0 <= val["confidence"] <= 1.0, key


def test_field_confidence_is_conservative():
    assert field_confidence(ResolutionState.EXPLICIT) == 0.95
    assert field_confidence(ResolutionState.EXTRACTED) == 0.80
    assert field_confidence(ResolutionState.INFERRED_WITH_STRONG_EVIDENCE) == 0.70
    assert field_confidence(ResolutionState.NOT_FOUND) == 0.0
    assert field_confidence(ResolutionState.AMBIGUOUS) == 0.0
    assert field_confidence("nope") == 0.0


def test_system_prompt_unchanged_by_phase18():
    assert "Data Analyst" in SYSTEM_PROMPT and "Data Scientist" in SYSTEM_PROMPT
