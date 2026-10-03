"""
test_phase24_routing.py — Phase 24.1 two-tab role-based routing.

Offline only: no Apify, no Sheets, no network, no LLM calls. The writer is
exercised against in-memory FakeWS tabs; the live spreadsheet is never touched
(the "Data Analyst & Data Scientist" tab does not exist yet — creation is a
deferred, controlled step, and the writer mirrors the main-tab creation path).

Sections A–J: eligible ACCEPTs route to exactly one tab.
K–S: ineligible leads reach neither tab. T: recruiter exception routes by
category. U–W: cross-tab dedup. X–Z: safety and behavior preservation.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

@pytest.fixture(autouse=True)
def allow_ungated(monkeypatch):
    monkeypatch.setattr("app.config.ALLOW_UNGATED_PRODUCTION_WRITE", True, raising=False)


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.schemas import GateResult, LeadVerdict
from app.models import ClassifiedPost, RawPost
from app.production_gate import (
    DATA_PRODUCTION,
    ELIGIBLE,
    HOLD,
    MAIN_PRODUCTION,
    REJECT,
    final_category,
    route_category,
    route_production_post,
)
from app.sheets_writer import EXPECTED_HEADERS, SheetsWriter, WriteResult


# ── Builders ──────────────────────────────────────────────────────────────

def make_merged(text, author="", profile="", company="Acme",
                category="Founder's Office", role="Chief of Staff",
                hm="Rohan Mehta", hm_ev="named in post: Rohan Mehta",
                confidence=0.95):
    return ClassifiedPost(
        post_url="https://www.linkedin.com/posts/x-activity-9",
        post_date="2026-09-14", text=text, author_name=author,
        author_profile_url=profile, company_name=company,
        major_category=category, exact_role=role,
        hiring_manager_name=hm, hiring_manager_evidence=hm_ev,
        hiring_manager_linkedin="Unclear",
        location="Bangalore", employment_type="Full-time",
        experience_requirement="2-4 Years", india_relevance="India",
        market="India", source_link="https://www.linkedin.com/posts/x-activity-9",
        description=text[:400], key_points="",
        confidence=confidence, scraped_at="now",
        classification_reason="ACCEPT: test", status="New",
        job_card_company="Unclear", is_valid=True,
    )


def make_gate(category="Founder's Office", role="Chief of Staff",
              confidence=0.95, decision="ACCEPT", llm_status="SUCCESS",
              company="Acme"):
    verdict = LeadVerdict(
        decision=decision, confidence=confidence,
        is_current_job=True, is_genuine_hiring=True, is_target_role=True,
        major_category=category, india_relevance="India",
        employment_type="Full-time", company=company,
        exact_role=role, reason="test verdict",
    )
    return GateResult(verdict=verdict, decision=decision,
                      status={"ACCEPT": "New", "REVIEW": "Review"}.get(decision, ""),
                      llm_status=llm_status)


def direct_company_lead(category="Founder's Office", role="Chief of Staff",
                        company="Acme", confidence=0.95):
    text = ("%s is hiring a %s in Bangalore. Full-time role. "
            "DM Rohan Mehta to apply." % (company, role))
    return (make_merged(text, author=company,
                        profile="https://www.linkedin.com/company/" + company.lower().replace(" ", ""),
                        company=company, category=category, role=role,
                        confidence=confidence),
            make_gate(category=category, role=role, confidence=confidence,
                      company=company))


class FakeWS:
    def __init__(self, urls=None, fail_with=None, title="ws"):
        self._urls = list(urls or [])
        self.appended = []
        self.fail_with = fail_with
        self.append_calls = 0
        self.title = title

    def col_values(self, n):
        return ["Source Link"] + list(self._urls)

    def row_values(self, n):
        return list(EXPECTED_HEADERS)

    def append_rows(self, rows, value_input_option=None):
        self.append_calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        self.appended.extend(rows)

    def append_row(self, row):
        self.appended.append(row)


def two_tab_writer(main_urls=None, data_urls=None, fail_main=None, fail_data=None):
    w = SheetsWriter.__new__(SheetsWriter)
    w.dry_run = False
    w.credentials_path = "mock"
    w.sheet_id = "mock"
    w.worksheet_name = "LinkedIn Hiring Leads"
    w.data_worksheet_name = "Data Analyst & Data Scientist"
    w.worksheet = FakeWS(urls=main_urls, fail_with=fail_main, title="main")
    w.data_worksheet = FakeWS(urls=data_urls, fail_with=fail_data, title="data")
    return w


def classified_as(url, category):
    return ClassifiedPost(
        post_url=url, post_date="", text="t", author_name="", author_profile_url="",
        company_name="Acme", major_category=category, exact_role="X",
        source_link=url, description="d", confidence=0.95, location="Bengaluru",
        employment_type="Full-time", experience_requirement="Not Specified",
        market="India", india_relevance="India", scraped_at="now",
        classification_reason="r", status="New", is_valid=True)


# ── A–G. Non-data ACCEPTs → main tab ──────────────────────────────────────
# NOTE (Phase 25.2): the pipeline taxonomy admits exactly four categories
# (Founder's Office / Chief of Staff / Data Analyst / Data Scientist).
# Founder Associate, Strategy, Growth, Generalist, VC … are EXACT-ROLE
# titles carried under an FO/CoS category — never category values. These
# params pin that title→MAIN mapping through real taxonomy values.

@pytest.mark.parametrize("category,role", [
    ("Founder's Office", "Founder's Office Associate"),   # A (§18.3)
    ("Chief of Staff", "Chief of Staff"),                 # B (§18.5)
    ("Founder's Office", "Founder Associate"),            # C (§18.4)
    ("Founder's Office", "Strategy Associate"),           # D (§18.6)
    ("Chief of Staff", "Growth Associate"),               # E (§18.7)
    ("Founder's Office", "Generalist"),                   # F (§18.8)
    ("Founder's Office", "VC Analyst"),                   # G (§18.9)
])
def test_non_data_accept_routes_to_main(category, role):
    m, g = direct_company_lead(category=category, role=role)
    # Non-taxonomy verdict categories coerce to None; stored category governs.
    res = route_production_post(m, g)
    assert res.destination == MAIN_PRODUCTION, res.reasons


# ── F–G. Data ACCEPTs → data tab ───────────────────────────────────────────

@pytest.mark.parametrize("category,role", [
    ("Data Analyst", "Data Analyst"),        # F
    ("Data Scientist", "Data Scientist"),    # G
])
def test_data_accept_routes_to_data(category, role):
    m, g = direct_company_lead(category=category, role=role)
    res = route_production_post(m, g)
    assert res.destination == DATA_PRODUCTION, res.reasons


# ── H–J. Exclusivity ───────────────────────────────────────────────────────

def test_data_analyst_never_routes_to_main():
    m, g = direct_company_lead(category="Data Analyst", role="Data Analyst")
    assert route_production_post(m, g).destination != MAIN_PRODUCTION  # H


def test_data_scientist_never_routes_to_main():
    m, g = direct_company_lead(category="Data Scientist", role="Data Scientist")
    assert route_production_post(m, g).destination != MAIN_PRODUCTION  # I


def test_founders_office_never_routes_to_data():
    m, g = direct_company_lead(category="Founder's Office",
                               role="Founder's Office Associate")
    assert route_production_post(m, g).destination != DATA_PRODUCTION  # J


def test_final_category_prefers_evaluated_verdict():
    m, g = direct_company_lead(category="Data Analyst", role="Data Analyst")
    # Verdict/Data conflict resolves to the evaluated verdict (FO here).
    g.verdict = g.verdict.model_copy(update={"major_category": "Founder's Office"})
    assert final_category(m, g) == "Founder's Office"
    assert route_production_post(m, g).destination == MAIN_PRODUCTION


def test_final_category_falls_back_when_verdict_none():
    m, g = direct_company_lead(category="Data Analyst", role="Data Analyst")
    g.verdict = g.verdict.model_copy(update={"major_category": "None"})
    assert final_category(m, g) == "Data Analyst"
    assert route_production_post(m, g).destination == DATA_PRODUCTION


def test_route_category_units():
    assert route_category("Data Analyst") == DATA_PRODUCTION      # §18.1
    assert route_category("Data Scientist") == DATA_PRODUCTION    # §18.2
    assert route_category("Founder's Office") == MAIN_PRODUCTION
    assert route_category("Chief of Staff") == MAIN_PRODUCTION
    # Phase 25.2 HARD invariant: unknown/ambiguous/conflicting categories
    # NEVER default to MAIN — they HOLD (§18.10/11).
    assert route_category("Strategy") == HOLD
    assert route_category("Venture Capital") == HOLD
    assert route_category("Generalist") == HOLD
    assert route_category("Business Analyst") == HOLD
    assert route_category("Product Analyst") == HOLD
    assert route_category("Marketing Manager") == HOLD
    assert route_category("None") == HOLD                        # §18.10
    assert route_category("") == HOLD
    assert route_category("Unclear") == HOLD
    assert route_category("N/A") == HOLD


# ── K–S. Ineligible → neither tab ──────────────────────────────────────────

def test_review_routes_nowhere():  # K
    m, g = direct_company_lead()
    g.decision = "REVIEW"
    g.status = "Review"
    res = route_production_post(m, g)
    assert res.destination == HOLD


def test_reject_routes_nowhere():  # L
    m, g = direct_company_lead()
    g.decision = "REJECT"
    g.status = ""
    res = route_production_post(m, g)
    assert res.destination == REJECT


def test_confidence_059_routes_nowhere():  # M
    m, g = direct_company_lead(confidence=0.59)
    assert route_production_post(m, g).destination == REJECT


def test_confidence_060_routes_correctly():  # N
    m, g = direct_company_lead(category="Data Scientist",
                               role="Data Scientist", confidence=0.60)
    assert route_production_post(m, g).destination == DATA_PRODUCTION


def test_unavailable_routes_nowhere():  # O
    m, g = direct_company_lead()
    g.llm_status = "UNAVAILABLE"
    res = route_production_post(m, g)
    assert res.destination == HOLD
    assert res.destination not in (MAIN_PRODUCTION, DATA_PRODUCTION)


def test_company_unclear_routes_nowhere():  # P
    m, g = direct_company_lead()
    m.company_name = "Unclear"
    assert route_production_post(m, g).destination not in (
        MAIN_PRODUCTION, DATA_PRODUCTION)


def test_hm_unclear_routes_nowhere():  # Q
    m, g = direct_company_lead()
    m.hiring_manager_name = "Unclear"
    m.hiring_manager_evidence = ""
    assert route_production_post(m, g).destination not in (
        MAIN_PRODUCTION, DATA_PRODUCTION)


def test_advertising_poster_routes_nowhere():  # R
    m = make_merged(
        "XYZ Startup is hiring a Founder's Office Associate. Apply here.",
        author="CareerJobs India",
        profile="https://www.linkedin.com/company/careerjobs-india",
        company="XYZ Startup")
    g = make_gate()
    assert route_production_post(m, g).destination == REJECT


def test_aggregator_routes_nowhere():  # S
    m = make_merged(
        "Jobs roundup: 10 roles hiring this week across startups. Links below.",
        author="rohan", profile="https://www.linkedin.com/in/rohan")
    assert route_production_post(m, make_gate()).destination == REJECT


# ── T. Recruiter exception routes by category ──────────────────────────────

def test_recruiter_for_company_routes_to_data():
    m = make_merged(
        "I'm a Technical Recruiter hiring a Data Analyst for Acme. "
        "Acme is hiring in Hyderabad. DM Neha Verma to apply.",
        author="Neha Verma", profile="https://www.linkedin.com/in/neha-verma",
        company="Acme", category="Data Analyst", role="Data Analyst",
        hm="Neha Verma", hm_ev="named in post: Neha Verma")
    g = make_gate(category="Data Analyst", role="Data Analyst")
    assert route_production_post(m, g).destination == DATA_PRODUCTION


def test_recruiter_for_company_routes_to_main():
    m = make_merged(
        "I'm a Technical Recruiter hiring a Chief of Staff for Acme. "
        "Acme is hiring in Bangalore. DM Neha Verma to apply.",
        author="Neha Verma", profile="https://www.linkedin.com/in/neha-verma",
        company="Acme", hm="Neha Verma", hm_ev="named in post: Neha Verma")
    assert route_production_post(m, make_gate()).destination == MAIN_PRODUCTION


# ── U–W. Cross-tab dedup ──────────────────────────────────────────────────

def test_duplicate_in_main_blocks_write():  # U
    w = two_tab_writer(main_urls=["https://x/dup"])
    assert "https://x/dup" in w.get_existing_urls()


def test_duplicate_in_data_blocks_write():  # V
    w = two_tab_writer(data_urls=["https://x/dup"])
    assert "https://x/dup" in w.get_existing_urls()


def test_cross_tab_union_and_fail_closed():  # W
    w = two_tab_writer(main_urls=["https://x/1"], data_urls=["https://x/2"])
    assert w.get_existing_urls() == {"https://x/1", "https://x/2"}
    w.data_worksheet = None
    # Legacy-shaped writer (no data tab) still reads the main tab.
    del w.data_worksheet
    assert w.get_existing_urls() == {"https://x/1"}
    bad = two_tab_writer()
    bad.data_worksheet.col_values = None
    import pytest as _p
    from app.sheets_writer import SheetReadError
    with _p.raises(SheetReadError):
        bad.get_existing_urls()


def test_writer_splits_batch_across_tabs():
    w = two_tab_writer()
    posts = [classified_as("https://x/fo", "Founder's Office"),
             classified_as("https://x/da", "Data Analyst"),
             classified_as("https://x/ds", "Data Scientist"),
             classified_as("https://x/cos", "Chief of Staff")]
    r = w.write_posts(posts)
    assert r.outcome == "SUCCESS" and r.written == 4
    assert w.worksheet.append_calls == 1 and w.data_worksheet.append_calls == 1
    assert len(w.worksheet.appended) == 2 and len(w.data_worksheet.appended) == 2
    assert r.destinations == {"LinkedIn Hiring Leads": 2,
                              "Data Analyst & Data Scientist": 2}
    main_links = {row[7] for row in w.worksheet.appended}
    data_links = {row[7] for row in w.data_worksheet.appended}
    assert main_links == {"https://x/fo", "https://x/cos"}
    assert data_links == {"https://x/da", "https://x/ds"}
    assert not (main_links & data_links)  # never duplicated across tabs


def test_writer_single_tab_fallback_unchanged():
    w = two_tab_writer()
    del w.data_worksheet  # pre-24.1 shaped writer
    posts = [classified_as("https://x/da", "Data Analyst")]
    r = w.write_posts(posts)
    assert r.outcome == "SUCCESS" and r.written == 1
    assert len(w.worksheet.appended) == 1


def test_writer_partial_failure_truthful():
    w = two_tab_writer(fail_data=RuntimeError("quota exceeded"))
    posts = [classified_as("https://x/fo", "Founder's Office"),
             classified_as("https://x/da", "Data Analyst")]
    r = w.write_posts(posts)
    # Main tab confirmed its row; data tab definitely failed. Truthful:
    # confirmed rows count, outcome FAILED, never SUCCESS.
    assert r.outcome == "FAILED" and r.success is False
    assert r.attempted == 2 and r.written == 1 and r.failed == 1
    assert r.destinations == {"LinkedIn Hiring Leads": 1}
    assert len(w.worksheet.appended) == 1  # main tab still landed


def test_writer_unknown_outcome_poisons_total():
    w = two_tab_writer(fail_data=TimeoutError("request timed out after send"))
    posts = [classified_as("https://x/fo", "Founder's Office"),
             classified_as("https://x/da", "Data Analyst")]
    r = w.write_posts(posts)
    # Ambiguity on one tab poisons the total to UNKNOWN, but the confirmed
    # main-tab row still counts (never claim 0 when a row landed).
    assert r.outcome == "UNKNOWN" and r.success is False
    assert r.written == 1 and r.destinations == {"LinkedIn Hiring Leads": 1}


# ── X–Z. Safety and preservation ───────────────────────────────────────────

def test_existing_rows_untouched_only_appends():  # X
    w = two_tab_writer(main_urls=["https://x/old"])
    w.write_posts([classified_as("https://x/new", "Founder's Office")])
    assert w.worksheet._urls == ["https://x/old"]
    assert [row[7] for row in w.worksheet.appended] == ["https://x/new"]


def test_row_mapping_23_columns_identical():  # Y
    w = two_tab_writer()
    row = w._build_row(classified_as("https://x/1", "Data Scientist"))
    assert len(row) == 23
    assert len(EXPECTED_HEADERS) == 23
    assert row[1] == "Data Scientist" and row[7] == "https://x/1"


def test_default_gate_off_behavior_unchanged():  # Z
    from app.orchestrator import Orchestrator
    with patch("app.orchestrator.DataDopingSource"), \
         patch("app.orchestrator.DeterministicClassifier"), \
         patch("app.orchestrator.SheetsWriter"):
        o = Orchestrator()
        assert o.production_gate_enabled is False


def test_orchestrator_reports_per_tab_counts():
    from app.orchestrator import Orchestrator
    url_main, url_data = "https://x/m", "https://x/d"

    def classify(post):
        cat = ("Data Analyst" if post.post_url == url_data
               else "Founder's Office")
        return ClassifiedPost(
            post_url=post.post_url, post_date="", text="t", author_name="",
            author_profile_url="", company_name="Acme", major_category=cat,
            exact_role="X", source_link=post.post_url, description="d",
            confidence=0.95, location="Bengaluru", employment_type="Full-time",
            experience_requirement="Not Specified", market="India",
            india_relevance="India", scraped_at="now",
            classification_reason="r", status="New", is_valid=True)

    with patch("app.orchestrator.DataDopingSource"), \
         patch("app.orchestrator.DeterministicClassifier"), \
         patch("app.orchestrator.SheetsWriter"):
        o = Orchestrator()
        o.source.search_all.return_value = [
            RawPost(post_url=u, post_date="", author_name="",
                    author_profile_url="", text="t")
            for u in (url_main, url_data)]
        o.classifier.classify.side_effect = classify

        class V:
            llm_calls = 0

            def verify(self, **kw):
                self.llm_calls += 1
                from app.llm.schemas import GateResult as GR, LeadVerdict as LV
                return GR(verdict=LV(decision="ACCEPT", confidence=0.95,
                                     is_current_job=True, is_genuine_hiring=True,
                                     is_target_role=True,
                                     major_category="Founder's Office",
                                     india_relevance="India",
                                     employment_type="Full-time"),
                          decision="ACCEPT", status="New")

        o.verifier = V()
        o.sheets_writer.get_existing_urls.return_value = set()

        from app.sheets_writer import WriteResult as WR
        o.sheets_writer.write_posts.return_value = WR(
            attempted=2, written=2, failed=0, success=True, outcome="SUCCESS",
            destinations={"LinkedIn Hiring Leads": 1,
                          "Data Analyst & Data Scientist": 1})
        summary = o.run_pipeline(limit=10)
        assert summary["main_written"] == 1 and summary["data_written"] == 1
        assert summary["written_rows"] == 2


# ── Phase 25.2 strict routing (§18). Offline only. ──────────────────────────
# HARD invariant: DATA↔data-tab, FO/CoS↔main-tab, everything else→HOLD.
# A post can never appear in both tabs; row category cells always agree
# with their tab. Builder for a fully-evidenced eligible pair:

def _eligible_pair(url, category, role):
    text = ("Acme is hiring a %s in Bangalore. Full-time role. "
            "DM Rohan Mehta to apply." % role)
    m = make_merged(text, author="Acme",
                    profile="https://www.linkedin.com/company/acme",
                    company="Acme", category=category, role=role,
                    confidence=0.95)
    m.post_url = url
    m.source_link = url
    g = make_gate(category=category, role=role, confidence=0.95,
                  company="Acme")
    return m, g


def test_252_unknown_category_holds_end_to_end():  # §18.11
    for cat in ("Business Analyst", "Product Analyst", "Strategy",
                "Venture Capital", "Generalist", "Marketing Manager"):
        m, g = direct_company_lead(category=cat, role="Associate")
        res = route_production_post(m, g)
        assert res.destination == HOLD, (cat, res.reasons)


def test_252_missing_category_holds():  # §18.10
    m, g = direct_company_lead(category="", role="Associate")
    assert route_production_post(m, g).destination == HOLD
    m2, g2 = direct_company_lead(category="None", role="Associate")
    assert route_production_post(m2, g2).destination == HOLD


def test_252_not_run_null_confidence_holds():  # §18.19
    m, g = direct_company_lead()
    g.llm_status = "NOT_RUN"  # llm_confidence derives None ⇒ never production
    assert g.llm_confidence is None
    res = route_production_post(m, g)
    assert res.destination == HOLD
    assert res.destination not in (MAIN_PRODUCTION, DATA_PRODUCTION)


def test_252_data_lead_never_written_to_main():  # §18.12
    w = two_tab_writer()
    m, g = _eligible_pair("https://x/da1", "Data Analyst", "Data Analyst")
    r = w.write_production([(m, g)])
    assert r.outcome == "SUCCESS" and r.written == 1 and r.routing_dropped == 0
    assert len(w.worksheet.appended) == 0  # MAIN untouched
    assert len(w.data_worksheet.appended) == 1
    assert w.data_worksheet.appended[0][1] == "Data Analyst"  # cell == tab


def test_252_fo_lead_never_written_to_data():  # §18.14
    w = two_tab_writer()
    m, g = _eligible_pair("https://x/fo1", "Founder's Office",
                          "Founder Associate")
    r = w.write_production([(m, g)])
    assert r.outcome == "SUCCESS" and r.written == 1 and r.routing_dropped == 0
    assert len(w.data_worksheet.appended) == 0  # DATA untouched
    assert len(w.worksheet.appended) == 1
    assert w.worksheet.appended[0][1] == "Founder's Office"  # cell == tab


def test_252_row_category_stamped_with_routing_category():
    # Stored FO + evaluated DS verdict: routes DATA and the printed cell
    # says Data Scientist (the authoritative final category), never FO.
    w = two_tab_writer()
    m, g = _eligible_pair("https://x/mix", "Founder's Office",
                          "Data Scientist")
    g.verdict = g.verdict.model_copy(
        update={"major_category": "Data Scientist"})
    r = w.write_production([(m, g)])
    assert r.outcome == "SUCCESS" and r.written == 1
    assert len(w.worksheet.appended) == 0
    assert w.data_worksheet.appended[0][1] == "Data Scientist"


def test_252_same_lead_twice_written_once():  # §18.15
    w = two_tab_writer()
    m, g = _eligible_pair("https://x/dup", "Data Analyst", "Data Analyst")
    m2, g2 = _eligible_pair("https://x/dup", "Data Analyst", "Data Analyst")
    r = w.write_production([(m, g), (m2, g2)])
    assert r.written == 1
    main_links = {row[7] for row in w.worksheet.appended}
    data_links = {row[7] for row in w.data_worksheet.appended}
    assert not (main_links & data_links)
    assert len(w.data_worksheet.appended) == 1


def test_252_giri_historical_shape_never_production():  # §18.16/20
    # Pagaar-India ad poster + HM Unclear ACCEPT ⇒ dropped; existing rows kept.
    w = two_tab_writer(main_urls=["https://old/1"])
    m = make_merged(
        "GIRI CONSULTANCY Associate Data Scientist & ML Developer role. "
        "100% FREE APPLY DIRECTLY HERE https://lnkd.in/gKNKb4aX",
        author="Pagaar India",
        profile="https://www.linkedin.com/company/pagaar-india",
        company="GIRI CONSULTANCY", category="Data Scientist",
        role="Associate Data Scientist & ML Developer",
        hm="Unclear", hm_ev="")
    g = make_gate(category="Data Scientist",
                  role="Associate Data Scientist & ML Developer",
                  company="GIRI CONSULTANCY")
    r = w.write_production([(m, g)])
    assert r.written == 0
    assert len(w.worksheet.appended) == 0
    assert len(w.data_worksheet.appended) == 0
    assert w.worksheet._urls == ["https://old/1"]  # history untouched


def test_252_both_tabs_identical_23_col_schema():  # §18.23
    w = two_tab_writer()
    m1, g1 = _eligible_pair("https://x/m1", "Chief of Staff", "Chief of Staff")
    m2, g2 = _eligible_pair("https://x/d1", "Data Scientist", "Data Scientist")
    r = w.write_production([(m1, g1), (m2, g2)])
    assert r.outcome == "SUCCESS" and r.written == 2
    main_row = w.worksheet.appended[0]
    data_row = w.data_worksheet.appended[0]
    assert len(main_row) == 23 == len(data_row) == len(EXPECTED_HEADERS)
    assert main_row[1] == "Chief of Staff" and data_row[1] == "Data Scientist"


def test_252_enriched_post_folds_evaluated_category():  # §4 priority
    from app.llm.verifier import enriched_post
    m, g = direct_company_lead(category="Founder's Office",
                               role="Founder Associate")
    g.verdict = g.verdict.model_copy(
        update={"major_category": "Data Scientist"})
    assert enriched_post(m, g).major_category == "Data Scientist"
    # Unevaluated gate keeps the deterministic pipeline value.
    g.llm_status = "UNAVAILABLE"
    assert enriched_post(m, g).major_category == "Founder's Office"
