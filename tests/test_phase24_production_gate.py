"""
test_phase24_production_gate.py — Phase 24 direct-company + verified-HM gate.

Offline only: no Apify, no Sheets, no network, no LLM calls. Covers brief §12
A–Q directly against app.production_gate (R = the full suite, run separately).
Orchestrator integration is flag-gated: default OFF preserves current behavior
(pinned by pre-24 tests), ON enforces hold/reject accounting.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.schemas import GateResult, LeadVerdict
from app.models import ClassifiedPost, RawPost
from app.production_gate import (
    ELIGIBLE,
    HOLD,
    REJECT,
    PosterType,
    classify_poster,
    evaluate,
)


# ── Builders ──────────────────────────────────────────────────────────────

def make_merged(text, author="", profile="", company="Unclear",
                category="Founder's Office", role="Chief of Staff",
                hm="Unclear", hm_ev="", location="Bangalore",
                employment="Full-time", confidence=0.95):
    return ClassifiedPost(
        post_url="https://www.linkedin.com/posts/x-activity-1",
        post_date="2026-09-14", text=text, author_name=author,
        author_profile_url=profile, company_name=company,
        major_category=category, exact_role=role,
        hiring_manager_name=hm, hiring_manager_evidence=hm_ev,
        hiring_manager_linkedin="Unclear",
        location=location, employment_type=employment,
        experience_requirement="2-4 Years", india_relevance="India",
        market="India", source_link="https://www.linkedin.com/posts/x-activity-1",
        description=text[:400], key_points="",
        confidence=confidence, scraped_at="now",
        classification_reason="ACCEPT: test", status="New",
        job_card_company="Unclear", is_valid=True,
    )


def make_gate(confidence=0.95, decision="ACCEPT", llm_status="SUCCESS"):
    verdict = LeadVerdict(
        decision=decision, confidence=confidence,
        is_current_job=True, is_genuine_hiring=True, is_target_role=True,
        major_category="Founder's Office", india_relevance="India",
        employment_type="Full-time", company="Acme",
        exact_role="Chief of Staff", reason="test verdict",
    )
    return GateResult(verdict=verdict, decision=decision,
                      status={"ACCEPT": "New", "REVIEW": "Review"}.get(decision, ""),
                      llm_status=llm_status)


DIRECT_TEXT = ("Acme is hiring a Chief of Staff in Bangalore. Full-time role. "
               "DM Rohan Mehta to apply.")


def direct_merged(**over):
    kw = dict(text=DIRECT_TEXT, author="Acme",
              profile="https://www.linkedin.com/company/acme",
              company="Acme", hm="Rohan Mehta",
              hm_ev="named in post: Rohan Mehta")
    kw.update(over)
    return make_merged(**kw)


# ── A. Generic job advertising page → REJECT ──────────────────────────────

def test_advertising_page_reject():
    m = make_merged(
        text=("XYZ Startup is hiring a Founder's Office Associate. "
              "Apply here: https://lnkd.in/x"),
        author="CareerJobs India",
        profile="https://www.linkedin.com/company/careerjobs-india",
        company="XYZ Startup")
    res = evaluate(m, make_gate())
    assert not res.eligible and res.disposition == REJECT
    assert res.poster_type == PosterType.JOB_ADVERTISING_PAGE


# ── B. Aggregator → REJECT ────────────────────────────────────────────────

def test_aggregator_reject():
    m = make_merged(
        text=("Jobs roundup: 10 roles hiring this week across startups. "
              "Acme, Beta and Gamma all hiring. See comments for the full list."),
        author="rohan", profile="https://www.linkedin.com/in/rohan")
    res = evaluate(m, make_gate())
    assert not res.eligible and res.disposition == REJECT
    assert res.poster_type == PosterType.AGGREGATOR


# ── C. Third-party page naming a real company, no relationship → not eligible

def test_third_party_company_mention_without_relationship_held():
    m = make_merged(
        text=("Acme is hiring a Chief of Staff in Bangalore. Sharing for reach. "
              "Apply on their careers page."),
        author="Ravi Kumar", profile="https://www.linkedin.com/in/ravi-kumar",
        company="Acme")
    res = evaluate(m, make_gate())
    assert not res.eligible  # HM missing + unknown source ⇒ HOLD, never ACCEPT
    assert res.disposition == HOLD


# ── D. Direct company + company + HM → eligible ───────────────────────────

def test_direct_company_eligible():
    res = evaluate(direct_merged(), make_gate())
    assert res.eligible and res.disposition == ELIGIBLE
    assert res.poster_type == PosterType.DIRECT_COMPANY


# ── E. Hiring-manager post + company + HM → eligible ──────────────────────

def test_hiring_manager_post_eligible():
    m = make_merged(
        text=("I'm hiring a Chief of Staff at Acme. Bangalore, full-time. "
              "DM me to apply."),
        author="Rohan Mehta", profile="https://www.linkedin.com/in/rohan-mehta",
        company="Acme", hm="Rohan Mehta",
        hm_ev="author states hiring side: 'i'm hiring'")
    res = evaluate(m, make_gate())
    assert res.eligible and res.disposition == ELIGIBLE
    assert res.poster_type == PosterType.HIRING_MANAGER


# ── F. Employee + strong evidence → eligible ──────────────────────────────

def test_employee_strong_evidence_eligible():
    m = make_merged(
        text=("We are hiring a Founder Associate at Acme. Join our team in Mumbai. "
              "Full-time."),
        author="Sonia", profile="https://www.linkedin.com/in/sonia",
        company="Acme", hm="Sonia",
        hm_ev="author states hiring side: 'we are hiring'",
        role="Founder Associate")
    res = evaluate(m, make_gate())
    assert res.eligible and res.disposition == ELIGIBLE
    assert res.poster_type == PosterType.EMPLOYEE


# ── G. Recruiter + explicit company + hiring evidence → eligible ──────────

def test_recruiter_explicit_company_eligible():
    m = make_merged(
        text=("I'm a Technical Recruiter hiring a Data Analyst for Acme. "
              "Acme is hiring in Hyderabad. DM Neha Verma to apply."),
        author="Neha Verma", profile="https://www.linkedin.com/in/neha-verma",
        company="Acme", category="Data Analyst", role="Data Analyst",
        hm="Neha Verma", hm_ev="named in post: Neha Verma")
    res = evaluate(m, make_gate())
    assert res.eligible and res.disposition == ELIGIBLE
    assert res.poster_type == PosterType.RECRUITER_FOR_COMPANY


# ── H. Recruiter, undisclosed client → HOLD ───────────────────────────────

def test_recruiter_undisclosed_client_hold():
    m = make_merged(
        text=("Hiring a Data Analyst for our client (confidential). "
              "DM to apply with your resume."),
        author="Neha Verma", profile="https://www.linkedin.com/in/neha-verma",
        category="Data Analyst", role="Data Analyst")
    res = evaluate(m, make_gate())
    assert not res.eligible and res.disposition == HOLD


# ── I. Company missing → HOLD ─────────────────────────────────────────────

def test_company_missing_hold():
    m = make_merged(
        text="I'm hiring a Chief of Staff in Bangalore. DM me to apply.",
        author="Rohan Mehta", profile="https://www.linkedin.com/in/rohan-mehta",
        hm="Rohan Mehta", hm_ev="author states hiring side: 'i'm hiring'")
    res = evaluate(m, make_gate())
    assert not res.eligible and res.disposition == HOLD
    assert any("company" in r for r in res.reasons)


# ── J. Hiring manager missing → HOLD ──────────────────────────────────────

def test_hm_missing_hold():
    m = direct_merged(hm="Unclear", hm_ev="")
    res = evaluate(m, make_gate())
    assert not res.eligible and res.disposition == HOLD
    assert any("hiring manager" in r for r in res.reasons)


# ── K. Weak-context company inference → HOLD ──────────────────────────────

def test_weak_company_inference_hold():
    m = make_merged(
        text=("Ex-zerodha founder hiring a Chief of Staff in Bangalore. "
              "Stealth startup, full-time. DM to apply."),
        author="Rohan Mehta", profile="https://www.linkedin.com/in/rohan-mehta",
        company="Zerodha", hm="Rohan Mehta",
        hm_ev="author states hiring side: 'hiring'")
    res = evaluate(m, make_gate())
    assert not res.eligible and res.disposition == HOLD


# ── L. Confidence 0.59 → REJECT ───────────────────────────────────────────

def test_confidence_below_floor_reject():
    res = evaluate(direct_merged(), make_gate(confidence=0.59))
    assert not res.eligible and res.disposition == REJECT


# ── M. Confidence 0.60 + all gates → eligible ─────────────────────────────

def test_confidence_at_floor_eligible():
    res = evaluate(direct_merged(), make_gate(confidence=0.60))
    assert res.eligible and res.disposition == ELIGIBLE


# ── N. LLM UNAVAILABLE + null confidence → never ACCEPT ───────────────────

def test_unavailable_never_accept():
    g = make_gate(confidence=0.0, decision="REVIEW", llm_status="UNAVAILABLE")
    assert g.llm_confidence is None
    res = evaluate(direct_merged(), g)
    assert not res.eligible and res.disposition == HOLD


# ── O. Multi-company roundup → REJECT ─────────────────────────────────────

def test_multi_company_roundup_reject():
    m = make_merged(
        text=("Startup jobs roundup: Acme is hiring engineers, Beta is hiring "
              "designers, Gamma is hiring analysts. 10 fresh roles, links below."),
        author="Startup Jobs Daily",
        profile="https://www.linkedin.com/company/startup-jobs-daily",
        company="Acme")
    res = evaluate(m, make_gate())
    assert not res.eligible and res.disposition == REJECT


# ── P. Job list / advertising post → REJECT ───────────────────────────────

def test_job_list_advertising_reject():
    m = make_merged(
        text=("Freshers jobs: Java, Python, Data Analyst openings across India. "
              "Apply here daily."),
        author="Freshers Jobs India",
        profile="https://www.linkedin.com/company/freshers-jobs-india",
        category="Data Analyst", role="Data Analyst")
    res = evaluate(m, make_gate())
    assert not res.eligible and res.disposition == REJECT


# ── Q. Exact role + actual company + verified HM → passes ─────────────────

def test_full_triple_passes():
    m = direct_merged()
    res = evaluate(m, make_gate())
    assert res.eligible
    assert res.checks["company_ok"] and res.checks["hm_ok"] and res.checks["role_ok"]
    blob = " ".join(res.reasons)
    assert "Acme" in blob and "Rohan Mehta" in blob


def test_vague_role_hold():
    m = direct_merged(role="Multiple openings")
    res = evaluate(m, make_gate())
    assert not res.eligible and res.disposition == HOLD


# ── Poster-unit pins ──────────────────────────────────────────────────────

def test_real_surname_not_flagged_as_ad():
    p = classify_poster("Steve Jobs", "https://www.linkedin.com/in/steve-jobs",
                        "Acme is hiring engineers.", "Acme", "", "")
    assert p["poster_type"] != PosterType.JOB_ADVERTISING_PAGE


def test_company_page_mismatch_is_advertising():
    p = classify_poster("Corporate Career Page",
                        "https://www.linkedin.com/company/corporate-career-page",
                        "Acme is hiring a Chief of Staff.", "Acme", "", "")
    assert p["poster_type"] == PosterType.JOB_ADVERTISING_PAGE


def test_pagaar_shaped_poster_is_advertising():
    p = classify_poster("Pagaar India", "",
                        "Acme is hiring a Data Analyst.", "Acme", "", "")
    assert p["poster_type"] == PosterType.JOB_ADVERTISING_PAGE


def test_non_accept_never_promoted():
    res = evaluate(direct_merged(),
                   make_gate(confidence=0.95, decision="REVIEW", llm_status="REVIEW"))
    assert not res.eligible


# ── Orchestrator integration (flag-gated) ─────────────────────────────────

def _orch_gate(company="Acme", hm="Unclear", hm_ev="", confidence=0.95):
    verdict = LeadVerdict(
        decision="ACCEPT", confidence=confidence, is_current_job=True,
        is_genuine_hiring=True, is_target_role=True,
        major_category="Chief of Staff", india_relevance="India",
        employment_type="Full-time", company=company,
        exact_role="Chief of Staff", hiring_manager_name=hm,
        hiring_manager_evidence=hm_ev, reason="accept")
    return GateResult(verdict=verdict, decision="ACCEPT", status="New")


def _orch_classified(url, text, author="", profile=""):
    return ClassifiedPost(
        post_url=url, post_date="", text=text, author_name=author,
        author_profile_url=profile, major_category="Chief of Staff",
        market="India", source_link=url, confidence=0.95, scraped_at="now",
        classification_reason="deterministic pass", is_valid=True)


def _run_orch(flag, text, author="", profile="", company="Acme",
              hm="Unclear", hm_ev="", confidence=0.95, live_writer=False):
    from app.orchestrator import Orchestrator
    url = "https://lnkd.in/pg24"
    with patch("app.orchestrator.DataDopingSource"), \
         patch("app.orchestrator.DeterministicClassifier"), \
         patch("app.orchestrator.SheetsWriter"):
        o = Orchestrator(production_gate=flag)
        if live_writer:
            o.sheets_writer.dry_run = False  # simulate a REAL writer
        raw = RawPost(post_url=url, post_date="", author_name=author,
                      author_profile_url=profile, text=text)
        o.source.search_all.return_value = [raw]
        o.classifier.classify.return_value = _orch_classified(
            url, text, author, profile)

        class V:
            llm_calls = 0

            def verify(self, **kw):
                self.llm_calls += 1
                return _orch_gate(company, hm, hm_ev, confidence)

        o.verifier = V()
        o.sheets_writer.get_existing_urls.return_value = set()
        return o, o.run_pipeline(limit=10)


def test_orchestrator_default_off_preserves_behavior():
    from app.orchestrator import Orchestrator
    with patch("app.orchestrator.DataDopingSource"), \
         patch("app.orchestrator.DeterministicClassifier"), \
         patch("app.orchestrator.SheetsWriter"):
        o = Orchestrator()
        assert o.production_gate_enabled is False


def test_orchestrator_gate_on_holds_company_unclear_accept():
    # Phase 24.2 boundary: a held ACCEPT-path lead joins Review as an
    # INTERNAL artifact row only — it never reaches either production tab,
    # so no writer call happens and new_rows stays 0 (never New).
    o, summary = _run_orch(True, "We are hiring a Chief of Staff in Pune. Apply now.")
    assert summary["accepted"] == 0 and summary["review"] == 1
    assert summary["production_held"] == 1
    assert summary["new_rows"] == 0  # held rows never reach the write phase
    o.sheets_writer.write_posts.assert_not_called()
    o.sheets_writer.write_production.assert_not_called()


def test_orchestrator_gate_on_accepts_fully_evidenced_lead():
    # Phase 24.2 boundary: eligible pairs travel via write_production (which
    # re-validates every pair fail-closed), never via legacy write_posts.
    text = ("Acme is hiring a Chief of Staff in Bangalore. Full-time. "
            "DM Rohan Mehta to apply.")
    o, summary = _run_orch(True, text, author="Acme",
                           profile="https://www.linkedin.com/company/acme",
                           hm="Rohan Mehta", hm_ev="named in post: Rohan Mehta")
    assert summary["accepted"] == 1
    assert summary["production_held"] == 0 and summary["production_rejected"] == 0
    o.sheets_writer.write_posts.assert_not_called()
    pairs = o.sheets_writer.write_production.call_args[0][0]
    assert len(pairs) == 1
    assert [p.status for (p, _g) in pairs] == ["New"]


def test_orchestrator_gate_on_rejects_sub_floor_confidence():
    text = ("Acme is hiring a Chief of Staff in Bangalore. Full-time. "
            "DM Rohan Mehta to apply.")
    o, summary = _run_orch(True, text, author="Acme",
                           profile="https://www.linkedin.com/company/acme",
                           hm="Rohan Mehta", hm_ev="named in post: Rohan Mehta",
                           confidence=0.59)
    assert summary["accepted"] == 0 and summary["llm_rejected"] == 1
    assert summary["production_rejected"] == 1
    o.sheets_writer.write_posts.assert_not_called()


# ── Phase 25.1 production-invocation guard ────────────────────────────────
# A REAL (non-dry) Sheet write through the legacy gate-off path must be
# impossible by accident. These tests pin the fail-closed behavior.

def _run_orch_live_writer(flag, monkeypatch, allow_ungated=False):
    """Gate `flag`, but with a LIVE (non-dry) writer instead of the default."""
    import app.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "ALLOW_UNGATED_PRODUCTION_WRITE",
                        allow_ungated, raising=True)
    return _run_orch(flag, "We are hiring a Chief of Staff in Pune. Apply now.",
                     live_writer=True)


def test_live_write_gate_off_is_refused(monkeypatch):
    from app.orchestrator import Orchestrator
    from unittest.mock import patch
    from app.models import RawPost
    with patch("app.orchestrator.DataDopingSource"), \
         patch("app.orchestrator.DeterministicClassifier"), \
         patch("app.orchestrator.SheetsWriter"):
        import app.orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "ALLOW_UNGATED_PRODUCTION_WRITE",
                            False, raising=True)
        o = Orchestrator(production_gate=False)
        o.sheets_writer.dry_run = False  # live writer, gate off
        o.source.search_all.return_value = [
            RawPost(post_url="https://lnkd.in/guard", post_date="",
                    author_name="", author_profile_url="", text="x")]
        summary = o.run_pipeline(limit=10)
    assert summary["write_outcome"] == "GATE_REQUIRED"
    assert summary["errors"] == 1
    assert summary["scraped"] == 0  # refused BEFORE any Apify spend
    o.source.search_all.assert_not_called()
    o.sheets_writer.write_posts.assert_not_called()
    o.sheets_writer.write_production.assert_not_called()


def test_live_write_gate_on_proceeds(monkeypatch):
    import app.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "ALLOW_UNGATED_PRODUCTION_WRITE",
                        False, raising=True)
    o, summary = _run_orch_live_writer(True, monkeypatch)
    # Held lead: no live append happens in this fixture, but the run itself
    # must NOT be refused — the gate (not the guard) decides the outcome.
    assert summary["write_outcome"] != "GATE_REQUIRED"
    assert summary["production_held"] == 1


def test_live_write_gate_off_explicit_opt_out_proceeds(monkeypatch):
    import app.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "ALLOW_UNGATED_PRODUCTION_WRITE",
                        True, raising=True)
    o, summary = _run_orch_live_writer(False, monkeypatch, allow_ungated=True)
    assert summary["write_outcome"] != "GATE_REQUIRED"
    # Legacy gate-off path preserved byte-for-byte when explicitly allowed.
    o.sheets_writer.write_posts.assert_called_once()


def test_dry_run_gate_off_never_refused(monkeypatch):
    import app.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "ALLOW_UNGATED_PRODUCTION_WRITE",
                        False, raising=True)
    o, summary = _run_orch(False, "We are hiring a Chief of Staff in Pune. Apply now.")
    assert summary["write_outcome"] != "GATE_REQUIRED"
