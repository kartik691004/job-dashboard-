"""
test_historical_reprocessor.py — offline tests for the historical reprocessor.

Reuses the REAL production DeterministicClassifier but injects a scripted LLM
provider so no network / Groq calls occur. Verifies normalization, dedup,
deterministic-reject short-circuit, ACCEPT/REVIEW/REJECT routing, and that a
simulated LLM API failure degrades to REVIEW (never ACCEPT).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from app.llm.base import LLMError
from app.llm.verifier import Verifier
from historical_reprocessor import normalize_item, reprocess


class ScriptedProvider:
    """Duck-typed stand-in for an LLMProvider. Returns canned verdicts."""
    def __init__(self, mode):
        self.mode = mode  # accept | review | reject | error
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        if self.mode == "error":
            raise LLMError("simulated provider failure")
        decision = {"accept": "ACCEPT", "review": "REVIEW",
                    "reject": "REJECT"}[self.mode]
        return {
            "decision": decision,
            "confidence": 0.95,
            "is_current_job": True,
            "is_genuine_hiring": True,
            "is_target_role": True,
            "major_category": "Founder's Office",
            "india_relevance": "India",
            "employment_type": "Full-time",
            "company": "Acme",
            "exact_role": "Founder's Office Associate",
            "reason": "scripted verdict",
        }


def _fo_item(url, text, **kw):
    it = {
        "post_url": url,
        "text": text,
        "author": {"name": "A", "profile_url": "https://linkedin.com/in/a?x=1"},
        "postedAtISO": "2026-08-18T00:00:00Z",
    }
    it.update(kw)
    return it


def test_normalize_item_maps_production_fields():
    it = _fo_item(
        "https://www.linkedin.com/posts/x_activity-1?q=1",
        "We are hiring a Founder's Office in Mumbai",
        content={"type": "job", "description": "Mumbai, India",
                 "subtitle": "Job by Acme"},
    )
    rp = normalize_item(it)
    assert rp is not None
    assert rp.post_url == "https://www.linkedin.com/posts/x_activity-1"
    assert rp.text.startswith("We are hiring")
    assert rp.company == "Acme"
    assert rp.job_card_location == "Mumbai, India"
    assert rp.post_date.startswith("2026-08-18")


def test_normalize_drops_empty():
    assert normalize_item({"post_url": "", "text": ""}) is None


def test_dedup_by_normalized_url():
    a = _fo_item("https://www.linkedin.com/p/x?a=1",
                 "We are hiring a Founder's Office Associate in Bengaluru")
    b = _fo_item("https://www.linkedin.com/p/x?b=2",
                 "We are hiring a Founder's Office Associate in Bengaluru")
    recs, rep = reprocess([a, b], verifier=Verifier(provider=ScriptedProvider("accept")))
    assert rep["duplicates"] == 1
    assert rep["unique_after_dedup"] == 1
    assert rep["accepted"] == 1


def test_deterministic_reject_skips_verifier():
    it = _fo_item("https://www.linkedin.com/p/y",
                  "We are hiring a software engineer in Bangalore")
    recs, rep = reprocess([it], verifier=Verifier(provider=ScriptedProvider("accept")))
    assert rep["deterministic_candidates"] == 0
    assert rep["llm_calls"] == 0
    assert recs[0]["verification_status"] == "REJECT"
    assert "No relevant role keyword" in recs[0]["rejection_review_reason"]


def test_valid_accept():
    it = _fo_item("https://www.linkedin.com/p/z",
                  "We're hiring a Founder's Office Associate in Delhi, full-time")
    recs, rep = reprocess([it], verifier=Verifier(provider=ScriptedProvider("accept")))
    assert rep["deterministic_candidates"] == 1
    assert rep["accepted"] == 1
    assert recs[0]["verification_status"] == "ACCEPT"
    assert recs[0]["major_category"] == "Founder's Office"


def test_valid_review():
    it = _fo_item("https://www.linkedin.com/p/r",
                  "We're #hiring a Chief of Staff in Pune")
    recs, rep = reprocess([it], verifier=Verifier(provider=ScriptedProvider("review")))
    assert rep["review"] == 1
    assert recs[0]["verification_status"] == "REVIEW"


def test_valid_reject():
    it = _fo_item("https://www.linkedin.com/p/rj",
                  "We're #hiring a Chief of Staff in Pune")
    recs, rep = reprocess([it], verifier=Verifier(provider=ScriptedProvider("reject")))
    assert rep["rejected"] == 1
    assert recs[0]["verification_status"] == "REJECT"


def test_llm_failure_degrades_to_review_not_accept():
    it = _fo_item("https://www.linkedin.com/p/lf",
                  "We're #hiring a Chief of Staff in Pune")
    recs, rep = reprocess([it], verifier=Verifier(provider=ScriptedProvider("error")))
    assert rep["llm_api_failures"] == 1
    assert rep["accepted"] == 0
    assert recs[0]["verification_status"] == "REVIEW"
    assert "LLM unavailable" in recs[0]["rejection_review_reason"]


def test_report_metrics_present():
    it = _fo_item("https://www.linkedin.com/p/m",
                  "We're hiring a Founder's Office Executive in Hyderabad")
    recs, rep = reprocess([it], verifier=Verifier(provider=ScriptedProvider("accept")))
    for k in ("total_input", "unique_after_dedup", "duplicates",
              "deterministic_candidates", "llm_calls", "accepted", "review",
              "rejected", "llm_api_failures", "processing_errors"):
        assert k in rep
