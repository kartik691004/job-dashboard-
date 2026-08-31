"""
test_orchestrator_gate.py — Phase-2 wiring: the LLM gate inside run_pipeline.

Everything external is patched: no Apify, no Groq, no Google Sheets.
Verifies the brief §25 metrics, Review/accept separation (clarification #1),
and that LLM-rejected candidates are never written.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.base import LLMError
from app.llm.schemas import GateResult, LeadVerdict
from app.models import ClassifiedPost, RawPost
from app.orchestrator import Orchestrator


def make_raw(url: str) -> RawPost:
    return RawPost(post_url=url, post_date="", author_name="",
                   author_profile_url="", text="We are hiring a Chief of Staff in Pune. Apply now.")


def make_classified(url: str) -> ClassifiedPost:
    return ClassifiedPost(
        post_url=url, post_date="", text="t", author_name="", author_profile_url="",
        major_category="Chief of Staff", market="India", source_link=url,
        confidence=0.9, scraped_at="now", classification_reason="deterministic pass",
        is_valid=True,
    )


def make_gate(decision: str) -> GateResult:
    verdict = LeadVerdict(
        decision=decision.lower() if decision != "REJECT" else "REJECT",
        confidence=0.95 if decision == "ACCEPT" else 0.80,
        is_current_job=True, is_genuine_hiring=True, is_target_role=True,
        major_category="Chief of Staff", india_relevance="India",
        employment_type="Full-time", company="Acme", exact_role="Chief of Staff",
        reason=f"gate says {decision}",
    )
    status = {"ACCEPT": "New", "REVIEW": "Review", "REJECT": ""}[decision]
    return GateResult(verdict=verdict, decision=decision, status=status)


class StubVerifier:
    def __init__(self, gates):
        self.gates = list(gates)
        self.llm_calls = 0

    def verify(self, **kwargs):
        self.llm_calls += 1
        return self.gates.pop(0)


@pytest.fixture
def orch():
    with patch("app.orchestrator.DataDopingSource") as mock_src, \
         patch("app.orchestrator.DeterministicClassifier"), \
         patch("app.orchestrator.SheetsWriter") as mock_writer_cls:
        o = Orchestrator()
        o.sheets_writer = mock_writer_cls.return_value
        o.sheets_writer.get_existing_urls.return_value = set()
        yield o


def test_gate_metrics_and_status_separation(orch):
    urls = [f"https://lnkd.in/{i}" for i in range(3)]
    orch.source.search_all.return_value = [make_raw(u) for u in urls]
    orch.classifier.classify.side_effect = [make_classified(u) for u in urls]
    orch.verifier = StubVerifier([make_gate("ACCEPT"), make_gate("REVIEW"),
                                  GateResult(verdict=LeadVerdict(decision="REJECT"),
                                             decision="REJECT", status="")])

    summary = orch.run_pipeline(limit=10)

    assert summary["scraped"] == 3 and summary["unique"] == 3
    assert summary["deterministic_candidates"] == 3
    assert summary["llm_calls"] == 3
    assert summary["accepted"] == 1
    assert summary["review"] == 1
    assert summary["llm_rejected"] == 1
    assert summary["valid"] == 1                       # confirmed leads only
    assert summary["new_rows"] == 2                    # New + Review written
    assert summary["errors"] == 0

    written = orch.sheets_writer.write_posts.call_args[0][0]
    assert sorted(p.status for p in written) == ["New", "Review"]
    # The rejected URL must not be anywhere in the write batch.
    assert all(p.source_link != urls[2] for p in written)


def test_scraper_failure_reports_zeroed_llm_metrics(orch):
    orch.source.search_all.side_effect = LLMError("apify down")
    orch.verifier = StubVerifier([])
    summary = orch.run_pipeline(limit=10)
    assert summary["errors"] == 1
    assert summary["deterministic_candidates"] == 0
    assert summary["llm_calls"] == 0


def test_sheet_duplicates_count_against_both_tiers(orch):
    url = "https://lnkd.in/dup"
    orch.source.search_all.return_value = [make_raw(url)]
    orch.classifier.classify.return_value = make_classified(url)
    orch.sheets_writer.get_existing_urls.return_value = {url}
    orch.verifier = StubVerifier([make_gate("ACCEPT")])

    summary = orch.run_pipeline(limit=10)
    assert summary["sheet_duplicates"] == 1
    assert summary["new_rows"] == 0
    orch.sheets_writer.write_posts.assert_not_called()
