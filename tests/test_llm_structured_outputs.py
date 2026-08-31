"""
test_llm_structured_outputs.py — Phase-9 Groq Structured Outputs integration.

Cross-checks the full GROQ-FIX path end to end WITHOUT a live API:

  1. Transport fix: GroqProvider now emits response_format json_schema
     (strict:false) built from LeadVerdict — NOT the legacy json_object route
     that produced HTTP 400 json_validate_failed on openai/gpt-oss-120b.
  2. Verifier->schema->hard-gate flow: a schema-shaped structured response is
     parsed, validated, and gated. All hard gates + conservative caps are
     exercised with a mocked provider returning the exact LeadVerdict shape a
     gpt-oss structured response would produce.
  3. Safe failures: malformed structured JSON and provider HTTP errors degrade
     to REVIEW via the real Verifier (no crash, no auto-accept).

No network. No .env changes. DRY_RUN is untouched.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.base import LLMError
from app.llm.groq_provider import GroqProvider
from app.llm.schemas import LeadVerdict
from app.llm.verifier import Verifier


FULL_STRUCTURED = {
    "decision": "ACCEPT",
    "confidence": 0.95,
    "is_current_job": True,
    "is_genuine_hiring": True,
    "is_target_role": True,
    "is_aggregator": False,
    "has_negation": False,
    "is_experience_requirement_only": False,
    "actual_role": "Founder's Office Associate",
    "major_category": "Founder's Office",
    "exact_role": "Founder's Office Associate",
    "company": "Acme Robotics",
    "india_relevance": "India",
    "location": "Mumbai",
    "employment_type": "Full-time",
    "experience_requirement": "2-4 years",
    "ctc": "18-22",
    "currency": "INR LPA",
    "hiring_manager_name": "Unclear",
    "hiring_manager_linkedin": "Unclear",
    "cold_email": "Not Available",
    "application_method": "DM to apply",
    "description": "x" * 320,
    "reason": "genuine current Founder's Office vacancy in India",
    "evidence": ["We are hiring a Founder's Office Associate in Mumbai"],
    "evidence_vacancy": "We are hiring a Founder's Office Associate",
    "evidence_role": "Founder's Office Associate",
    "evidence_company": "Acme Robotics",
    "evidence_location": "Mumbai",
    "evidence_employment": "Full-time",
    "evidence_experience": "",
    "evidence_ctc": "",
    "evidence_hiring_manager": "",
    "evidence_email": "",
}


class ScriptedProvider:
    """Returns dict(payload) or raises; mirrors an LLMProvider transport."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        if isinstance(self._payload, Exception):
            raise self._payload
        return dict(self._payload)


def run(payload):
    return Verifier(ScriptedProvider(payload)).verify(
        post_text="We're hiring a Founder's Office Associate in Mumbai.",
        post_url="https://lnkd.in/x",
    )


# ── 1. Transport sends json_schema, not json_object ──────────────────────────

def test_groq_transport_uses_json_schema_not_json_object():
    p = GroqProvider._structured_output_payload()
    assert p["type"] == "json_schema"
    assert p["json_schema"]["strict"] is False
    assert p["json_schema"]["name"] == "LeadVerdict"
    assert p["json_schema"]["schema"] == LeadVerdict.model_json_schema()


def test_every_leadverdict_field_is_in_the_grok_schema():
    schema = GroqProvider._structured_output_payload()["json_schema"]["schema"]
    for field in LeadVerdict.model_fields:
        assert field in schema["properties"]


# ── 2. Successful structured verdict -> hard-gate flow ───────────────────────

def test_genuine_fo_india_fulltime_highconfidence_accepts():
    gate = run(FULL_STRUCTURED)
    assert gate.decision == "ACCEPT" and gate.status == "New"
    assert LeadVerdict(**FULL_STRUCTURED)  # schema-shaped parse is valid


def test_genuine_chief_of_staff_india_fulltime_accepts():
    cos = dict(FULL_STRUCTURED,
               major_category="Chief of Staff",
               exact_role="Chief of Staff to CEO",
               actual_role="Chief of Staff to CEO",
               location="Bangalore",
               evidence=["Opening: Chief of Staff to CEO"],
               evidence_role="Chief of Staff to CEO",
               evidence_location="Bangalore")
    gate = run(cos)
    assert gate.decision == "ACCEPT" and gate.status == "New"


# ── 3. Hard gates (LLM cannot override) ─────────────────────────────────────

def test_non_target_role_rejects_even_if_llm_says_accept():
    gate = run(dict(FULL_STRUCTURED,
                    is_target_role=False, major_category="None",
                    actual_role="Marketing Manager",
                    evidence=["Hiring a Marketing Manager"]))
    assert gate.decision == "REJECT"
    assert any("not Founder's Office" in r for r in gate.reasons)


def test_internship_rejects_even_if_llm_says_accept():
    gate = run(dict(FULL_STRUCTURED, employment_type="Internship", confidence=0.99))
    assert gate.decision == "REJECT"
    assert any("internship" in r.lower() for r in gate.reasons)


def test_non_india_rejects_even_if_llm_says_accept():
    gate = run(dict(FULL_STRUCTURED, india_relevance="Not India", location="New York"))
    assert gate.decision == "REJECT"
    assert any("India" in r for r in gate.reasons)


def test_aggregator_rejects_even_if_llm_says_accept():
    gate = run(dict(FULL_STRUCTURED, is_aggregator=True))
    assert gate.decision == "REJECT"


# ── 4. Conservative caps -> REVIEW ───────────────────────────────────────────

def test_low_confidence_reviews():
    gate = run(dict(FULL_STRUCTURED, confidence=0.85))
    assert gate.decision == "REVIEW" and gate.status == "Review"


def test_contract_held_for_review_not_auto_accepted():
    gate = run(dict(FULL_STRUCTURED, employment_type="Contract", confidence=0.97))
    assert gate.decision == "REVIEW" and gate.status == "Review"


# ── 5. Safe failure: malformed structured response / HTTP error -> REVIEW ────

def test_malformed_structured_response_degrades_to_review_after_retry():
    class Malformed:
        def complete_json(self, system, user):
            return {"decision": 12345}  # not schema-valid -> Pydantic ValidationError

    gate = Verifier(Malformed()).verify(post_text="x")
    assert gate.decision == "REVIEW" and gate.status == "Review"
    assert "invalid LLM JSON structure" in gate.reasons[0]


def test_http_error_degrades_to_review():
    prov = ScriptedProvider(LLMError("Groq HTTP 400 (json_validate_failed)"))
    gate = Verifier(prov).verify(post_text="x")
    assert gate.decision == "REVIEW" and gate.status == "Review"
    assert gate.llm_called is True
    assert prov.calls == 2  # one retry, then fail-closed


def test_empty_provider_response_is_fail_closed():
    # A schema-shaped but empty payload yields safe defaults that all point to
    # REJECT (not target role, not India, not full-time) — never a spurious ACCEPT.
    gate = run({})
    assert gate.decision == "REJECT"
    assert any("not Founder's Office" in r for r in gate.reasons)
