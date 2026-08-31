"""
test_llm_evidence_protocol.py — Phase 7 LLM QUALITY PROTOCOL (question K) tests.

Covers the per-field evidence quotes added to LeadVerdict:
  - structured Pydantic coercion (malformed values -> "", never a crash)
  - evidence quotes retained and surfaced in the exported evidence_snippets
  - LLM can NEVER override hard gates (already partly covered in test_llm_gate;
    here we prove it with fabricated-per-field-evidence payloads)
  - timeout / API failure / malformed response all degrade to REVIEW, never ACCEPT

Offline: every payload is scripted; no Groq calls.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.base import LLMError
from app.llm.schemas import EVIDENCE_FIELDS, LeadVerdict
from app.llm.verifier import SYSTEM_PROMPT, Verifier, enriched_post
from tests.test_llm_gate import MUMBAI_FO_POST, FakeProvider, make_payload
from app.enrichment.enricher import Enricher
from app.models import RawPost
from app.classifier import DeterministicClassifier


def run_gate(payload_over=None, text=MUMBAI_FO_POST, error=None, fail_first=0):
    base = make_payload(**({} if payload_over is None else payload_over))
    prov = FakeProvider(payload=base, error=error or "boom", fail_first=fail_first)
    return Verifier(prov).verify(post_text=text), prov


# ── Q-K: per-field evidence is captured, coerced, never fabricated ───────────

def test_verdict_captures_per_field_evidence():
    v = LeadVerdict(**make_payload(
        evidence_vacancy="We are hiring a Founder's Office Associate in Mumbai",
        evidence_role="Founder's Office Associate",
        evidence_company="Acme Robotics is hiring",
        evidence_location="in Mumbai. Apply",
    ))
    assert v.evidence_vacancy == "We are hiring a Founder's Office Associate in Mumbai"
    assert v.evidence_role == "Founder's Office Associate"
    assert v.evidence_location == "in Mumbai. Apply"


def test_malformed_evidence_degrades_to_empty_never_fabricates():
    # The LLM sends a list/number/None for a quote field -> must become "".
    v = LeadVerdict(**make_payload(
        evidence_role=["Founder's Office Associate"],   # not a string
        evidence_company=12345,                          # not a string
        evidence_location=None,                          # None
    ))
    assert v.evidence_role == ""
    assert v.evidence_company == ""
    assert v.evidence_location == ""


def test_overlong_evidence_is_capped_to_quote_size():
    v = LeadVerdict(**make_payload(evidence_role="x" * 500))
    assert len(v.evidence_role) <= 300


def test_all_evidence_fields_are_defined_and_string():
    for f in EVIDENCE_FIELDS:
        assert isinstance(getattr(LeadVerdict(**make_payload()), f), str)


def test_missing_evidence_fields_default_empty_not_invented():
    v = LeadVerdict(**make_payload())
    assert v.evidence_email == ""          # no public email => no forensic quote


# ── Q-K: evidence is surfaced in the enrichment audit trail ──────────────────

def test_enrichment_surfaces_per_field_evidence():
    payload = make_payload(
        evidence_vacancy="We are hiring a Founder's Office Associate in Mumbai",
        evidence_role="Founder's Office Associate",
        evidence_email="",
    )
    prov = FakeProvider(payload=payload)
    gate = Verifier(prov).verify(post_text=MUMBAI_FO_POST)
    cp = DeterministicClassifier().classify(RawPost(
        post_url="https://lnkd.in/x", post_date="", author_name="Aditi",
        author_profile_url="in/aditi", text=MUMBAI_FO_POST,
    ))
    out = enriched_post(cp, gate)
    enriched = Enricher().enrich_from_classified(out, gate.verdict)
    trail = " ".join(s for s in enriched.evidence_snippets if isinstance(s, str))
    assert "evidence_vacancy: We are hiring" in trail
    assert "evidence_role: Founder's Office Associate" in trail


def test_no_evidence_means_no_fake_quotes_in_trail():
    payload = make_payload()   # all evidence_* empty
    prov = FakeProvider(payload=payload)
    gate = Verifier(prov).verify(post_text=MUMBAI_FO_POST)
    cp = DeterministicClassifier().classify(RawPost(
        post_url="https://lnkd.in/x", post_date="", author_name="Aditi",
        author_profile_url="in/aditi", text=MUMBAI_FO_POST,
    ))
    out = enriched_post(cp, gate)
    enriched = Enricher().enrich_from_classified(out, gate.verdict)
    assert not any(isinstance(s, str) and s.startswith("evidence_")
                   for s in enriched.evidence_snippets)


# ── Hard-gate supremacy: fabricated LLM evidence cannot ACCEPT a reject ──────

def test_llm_cannot_override_internship_ban_even_with_evidence():
    # The LLM confidently ACCEPTs an internship and even supplies evidence
    # quotes. The Python hard gate must still REJECT.
    gate, _ = run_gate(make_payload(
        decision="ACCEPT", confidence=0.99, employment_type="Internship",
        evidence_vacancy="Great internship opening!",
        evidence_role="Founder's Office Intern",
    ))
    assert gate.decision == "REJECT"
    assert any("internship" in r.lower() for r in gate.reasons)


def test_llm_cannot_override_non_target_role_even_with_evidence():
    gate, _ = run_gate(make_payload(
        decision="ACCEPT", confidence=0.99, is_target_role=False,
        major_category="None", exact_role="CRM Executive",
        evidence_role="We're hiring a CRM Executive",
    ))
    assert gate.decision == "REJECT"


def test_llm_cannot_override_non_india_even_with_evidence():
    gate, _ = run_gate(make_payload(
        decision="ACCEPT", confidence=0.99, india_relevance="Not India",
        location="New York", evidence_location="in New York City",
    ))
    assert gate.decision == "REJECT"


def test_llm_cannot_override_non_current_even_with_evidence():
    gate, _ = run_gate(make_payload(
        decision="ACCEPT", confidence=0.99, is_current_job=False,
        evidence_vacancy="We have been hiring for years",
    ))
    assert gate.decision == "REJECT"


# ── Provider failure modes (Q-K protocol): always degrade, never accept ──────

def test_llm_timeout_degrades_to_review():
    class TimeoutProvider:
        def complete_json(self, system, user):
            raise LLMError("Groq timeout after 30s")

    gate = Verifier(TimeoutProvider()).verify(post_text=MUMBAI_FO_POST)
    assert gate.decision == "REVIEW"


def test_llm_api_failure_degrades_to_review():
    class ApiDownProvider:
        def complete_json(self, system, user):
            raise LLMError("Groq HTTP 500")

    gate = Verifier(ApiDownProvider()).verify(post_text=MUMBAI_FO_POST)
    assert gate.decision == "REVIEW"


def test_malformed_json_degrades_to_review_not_accept():
    class BadProvider:
        def complete_json(self, system, user):
            return {"decision": 5, "confidence": "nine"}   # both invalid

    gate = Verifier(BadProvider()).verify(post_text=MUMBAI_FO_POST)
    assert gate.decision == "REVIEW"
    assert gate.status == "Review"


def test_prompt_asks_for_per_field_evidence():
    # The verifier prompt must instruct the LLM to produce Q-K evidence quotes.
    assert "evidence_vacancy" in SYSTEM_PROMPT
    assert "evidence_role" in SYSTEM_PROMPT
    assert "verbatim" in SYSTEM_PROMPT.lower() or "quote" in SYSTEM_PROMPT.lower()
    assert "never" in SYSTEM_PROMPT.lower()


def test_evidence_quote_is_short_ver_batim_fragment():
    # The final quote must be capped; a fabricated multi-sentence paraphrase is
    # still capped to the quote window (auditable, non-invented marker).
    v = LeadVerdict(**make_payload(evidence_company="A" + " long " * 200))
    assert len(v.evidence_company) <= 300
    assert "\n" not in v.evidence_company