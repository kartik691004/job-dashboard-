"""
test_data_quality_regression.py — real-dataset failure corpus.

Uses the SAME scripted-provider pattern as test_llm_gate.py but feeds the exact
failure cases the user found in production data. Each case asserts the FINAL
pipeline decision (Verifier + Python hard gate), proving the LLM cannot
override the hard rules (§13): a flawed ACCEPT is downgraded to REJECT/REVIEW.

No live Groq/Apify/Sheets. Offline only.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.base import LLMError
from app.llm.schemas import GateResult, LeadVerdict
from app.llm.verifier import Verifier


class FakeProvider:
    """Scripted LLMProvider: returns a canned payload (or raises)."""

    def __init__(self, payload=None, error="boom", fail_first=0):
        self.payload = payload if payload is not None else {}
        self.error = error
        self.fail_first = fail_first
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise LLMError(self.error)
        if isinstance(self.payload, Exception):
            raise self.payload
        return dict(self.payload)


def make_payload(**over):
    """A fully-accepting baseline; override per-case."""
    p = {
        "decision": "ACCEPT",
        "confidence": 0.97,
        "is_current_job": True,
        "is_genuine_hiring": True,
        "is_target_role": True,
        "is_aggregator": False,
        "has_negation": False,
        "is_experience_requirement_only": False,
        "major_category": "Founder's Office",
        "actual_role": "Founder's Office Associate",
        "exact_role": "Founder's Office Associate",
        "company": "Acme",
        "india_relevance": "India",
        "location": "Mumbai",
        "employment_type": "Full-time",
        "experience_requirement": "2-4 years",
        "ctc": "18-22",
        "currency": "INR LPA",
        "hiring_manager_name": "Unclear",
        "hiring_manager_linkedin": "Unclear",
        "cold_email": "Not Available",
        "application_method": "DM",
        "description": "x" * 320,
        "reason": "genuine current FO vacancy in India",
        "evidence": ["We are hiring a Founder's Office Associate in Mumbai"],
    }
    p.update(over)
    return p


def run(payload_over=None, provider=None):
    prov = provider if provider is not None else FakeProvider(make_payload(**(payload_over or {})))
    return Verifier(prov).verify(
        post_text="placeholder post text", post_url="https://lnkd.in/x",
        author_name="Aditi", author_profile_url="https://linkedin.com/in/aditi",
    )


# ── A. Razorpay — target role only as experience requirement ─────────────────
def test_A_razorpay_experience_only_rejected():
    # Even if the LLM naively returned ACCEPT, the gate must REJECT.
    gate = run(make_payload(
        decision="ACCEPT", is_target_role=False,
        is_experience_requirement_only=True, major_category="None",
        actual_role="Associate - Strategy", exact_role="Associate - Strategy",
        reason="Chief of Staff appears only as experience requirement",
    ))
    assert gate.decision == "REJECT"
    assert any("experience" in r.lower() for r in gate.reasons)


# ── B. Deployment Inc — explicit negation ─────────────────────────────────────
def test_B_deployment_inc_negation_rejected():
    gate = run(make_payload(
        decision="ACCEPT", is_target_role=False, has_negation=True,
        major_category="None", actual_role="Builder",
        exact_role="Builder", company="Deployment Inc",
        reason="post says NOT hiring a Chief of Staff; hiring a Builder",
    ))
    assert gate.decision == "REJECT"
    assert any("negation" in r.lower() for r in gate.reasons)


# ── C/D. Aggregators / roundups ──────────────────────────────────────────────
def test_C_findmethejob_aggregator_rejected():
    gate = run(make_payload(
        decision="ACCEPT", is_aggregator=True,
        reason="10 India roles hiring right now; lists FO among others",
    ))
    assert gate.decision == "REJECT"
    assert any("aggregator" in r.lower() for r in gate.reasons)


def test_D_careerbee_roundup_rejected():
    gate = run(make_payload(
        decision="ACCEPT", is_aggregator=True,
        reason="13 new roles from companies hiring this week",
    ))
    assert gate.decision == "REJECT"
    assert any("aggregator" in r.lower() for r in gate.reasons)


# ── E/F. Not a current opening (advice / company update) ─────────────────────
def test_E_foundersfind_advice_rejected():
    gate = run(make_payload(
        is_current_job=False, is_genuine_hiring=False, is_target_role=False,
        major_category="None", actual_role="Unclear",
        reason="career advice, no specific vacancy advertised",
    ))
    assert gate.decision == "REJECT"


def test_F_smytten_company_update_rejected():
    gate = run(make_payload(
        is_current_job=False, is_genuine_hiring=False, is_target_role=False,
        major_category="None", actual_role="Unclear",
        reason="team update: members already joined",
    ))
    assert gate.decision == "REJECT"


# ── G. TalentoIndia — wrong actual role (Head of Business Operations) ────────
def test_G_talentoindia_wrong_actual_role_not_accepted():
    gate = run(make_payload(
        is_target_role=False, major_category="None",
        actual_role="Head of Business Operations",
        exact_role="Head of Business Operations",
        reason="advertised vacancy is Head of Business Operations",
    ))
    # Must NOT be an ACCEPT (REJECT or REVIEW both acceptable, REJECT expected).
    assert gate.decision != "ACCEPT"


# ── H. CRM Executive (Founder's Office) — functional role wins ───────────────
def test_H_crm_executive_not_target_rejected():
    gate = run(make_payload(
        is_target_role=False, major_category="None",
        actual_role="CRM Executive", exact_role="CRM Executive (Founder's Office)",
        reason="CRM job that sits within the Founder's Office",
    ))
    assert gate.decision == "REJECT"


def test_H_crm_executive_genuine_fo_can_accept():
    # Genuinely cross-functional FO work, clearly the vacancy.
    gate = run(make_payload(
        is_target_role=True, major_category="Founder's Office",
        actual_role="Founder's Office Associate (CRM & Growth)",
        exact_role="Founder's Office Associate (CRM & Growth)",
        reason="actual vacancy is a cross-functional FO role",
    ))
    assert gate.decision == "ACCEPT"


# ── I. Growth Officer (Founder's Office) — functional role wins ──────────────
def test_I_growth_officer_not_target_rejected():
    gate = run(make_payload(
        is_target_role=False, major_category="None",
        actual_role="Growth Officer", exact_role="Growth Officer (Founder's Office)",
        reason="Growth Officer is the actual vacancy",
    ))
    assert gate.decision == "REJECT"


def test_I_growth_officer_genuine_fo_can_accept():
    gate = run(make_payload(
        is_target_role=True, major_category="Founder's Office",
        actual_role="Founder's Office Generalist (incl. Growth)",
        exact_role="Founder's Office Generalist (incl. Growth)",
        reason="genuine FO generalist role with growth responsibilities",
    ))
    assert gate.decision == "ACCEPT"


# ── §13 final validation: LLM ACCEPT overridden by hard rules ────────────────
def test_13_accept_overridden_when_actual_role_non_target():
    gate = run(make_payload(
        decision="ACCEPT", confidence=0.99, is_target_role=False,
        major_category="None", actual_role="Strategy Associate",
        exact_role="Strategy Associate",
    ))
    assert gate.decision == "REJECT"


def test_13_accept_overridden_when_internship():
    gate = run(make_payload(
        decision="ACCEPT", confidence=0.99, employment_type="Internship",
    ))
    assert gate.decision == "REJECT"
    assert any("internship" in r.lower() for r in gate.reasons)


def test_13_accept_overridden_when_india_unclear():
    gate = run(make_payload(
        decision="ACCEPT", confidence=0.99, india_relevance="Unclear",
    ))
    assert gate.decision == "REJECT"


def test_13_accept_overridden_when_part_time():
    gate = run(make_payload(
        decision="ACCEPT", confidence=0.99, employment_type="Part-time",
    ))
    assert gate.decision == "REVIEW"   # non-full-time held, never auto-accepted


# ── Genuine examples must still ACCEPT (§15 do-not-overcorrect) ──────────────
def _accepts(**over):
    gate = run(make_payload(**over))
    assert gate.decision == "ACCEPT", gate.reasons


def test_genuine_fo_associate():
    _accepts(major_category="Founder's Office", actual_role="Founder's Office Associate",
             exact_role="Founder's Office Associate", company="Luar Beauty")


def test_genuine_fo_associate_with_scope():
    _accepts(major_category="Founder's Office",
             actual_role="Associate - Founder's Office (Strategy & Partnerships)",
             exact_role="Associate - Founder's Office (Strategy & Partnerships)",
             company="Medulance")


def test_genuine_cos_to_ceo():
    _accepts(major_category="Chief of Staff", actual_role="Chief of Staff to CEO",
             exact_role="Chief of Staff to CEO", company="Amama Partners")


def test_genuine_cos_india_country_manager():
    _accepts(major_category="Chief of Staff",
             actual_role="Chief of Staff - India Country Manager's Office",
             exact_role="Chief of Staff - India Country Manager's Office",
             company="NXP")


# ── Extraction integrity: no truncated/keyword-substring roles ────────────────
def test_extraction_never_truncated_substring():
    # Verifier should carry the exact_role the LLM returned verbatim (prompt
    # instructs full title); assert we don't silently pass a garbage substring.
    gate = run(make_payload(
        actual_role="Associate - Founder's Office",
        exact_role="Associate - Founder's Office",
    ))
    assert gate.verdict.exact_role == "Associate - Founder's Office"
    assert "..." not in gate.verdict.exact_role
    assert gate.verdict.exact_role.endswith("Office")


# ── Phase 7: exact-role fragment hardening (enrichment layer) ────────────────
def test_fragment_exact_role_never_kept():
    # The classifier can hand enrichment a keyword-appendage string
    # ("We are hiring a Founder's Office Associate at Acme i"). The enrichment
    # layer must NOT keep that as the exact role; it must fall back to the
    # clean title capture.
    from app.enrichment.enricher import resolve_exact_role
    text = ("We are hiring a Founder's Office Associate at Acme in Mumbai. "
            "Apply now.")
    res = resolve_exact_role("We are hiring a Founder's Office Associate at Acme i",
                             "", "", text)
    assert res.exact_role != "We are hiring a Founder's Office Associate at Acme i"
    assert "Founder's Office Associate" in res.exact_role


def test_clean_exact_role_still_preserved():
    from app.enrichment.enricher import resolve_exact_role
    title = "Chief of Staff - India Country Manager's Office"
    res = resolve_exact_role(title, "", "", "hiring Chief of Staff - India Country Manager's Office in Noida")
    assert res.exact_role == title


def test_llm_actual_role_is_still_authoritative_despite_verb_lead():
    from app.enrichment.enricher import resolve_exact_role
    # The LLM's curated actual_role is trusted even when its surface form starts
    # with a hiring verb — it is the model's explicit, curated title.
    res = resolve_exact_role("", "We are hiring a Chief of Staff", "",
                             "We are hiring a Chief of Staff to CEO in Mumbai.")
    assert res.exact_role == "We are hiring a Chief of Staff"
