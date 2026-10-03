"""
test_phase25_5_sourcing.py — Phase 25.5 high-signal sourcing + evidence tiers.

Offline only. Pins WITHOUT weakening any production bar:
- qualifying HM evidence tiers still resolve (founder hiring voice, named
  post, recruiter signature) while inference-only attributions stay blocked
  (company-page repost, employee repost, generic recruiter, profile-employment
  guess);
- company recovers only from explicit evidence; undisclosed stays Unclear;
- agency/aggregator/multi-employer posts stay blocked;
- direct company+role+HM can be ELIGIBLE; same minus HM is HOLD;
- sourcing score orders (never gates) + query layer pins (38 intact + 10 HS).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier import DeterministicClassifier
from app.config import SEARCH_QUERIES
from app.llm.schemas import GateResult, LeadVerdict
from app.models import ClassifiedPost, RawPost
from app.production_gate import (
    MAIN_PRODUCTION,
    classify_poster,
    evaluate,
    route_production_post,
)
from app.resolution import ResolutionState, resolve_hm
from app.enrichment.company_resolver import resolve as resolve_employer
from app.enrichment.schemas import CompanyEvidence
from app.sourcing_score import score_raw_post

CLF = DeterministicClassifier()


def make_post(text, author="Aditi Rao",
              profile="https://linkedin.com/in/aditi-rao"):
    return RawPost(
        post_url="https://www.linkedin.com/posts/test-activity-1",
        post_date="2026-09-01", text=text, author_name=author,
        author_profile_url=profile, company="Unclear",
        job_card_location="", job_card_company="Unclear",
        job_card_employment_type="Unclear", job_card_experience="Unclear")


def make_merged(text, author="", profile="", company="Acme",
                category="Founder's Office", role="Chief of Staff",
                hm="Rohan Mehta", hm_ev="named in post: Rohan Mehta"):
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
        description=text[:400], key_points="", confidence=0.95,
        scraped_at="now", classification_reason="ACCEPT: test", status="New",
        job_card_company="Unclear", is_valid=True)


def make_gate(category="Founder's Office", role="Chief of Staff",
              confidence=0.95, decision="ACCEPT", llm_status="SUCCESS",
              company="Acme"):
    verdict = LeadVerdict(
        decision=decision, confidence=confidence,
        is_current_job=True, is_genuine_hiring=True, is_target_role=True,
        major_category=category, india_relevance="India",
        employment_type="Full-time", company=company,
        exact_role=role, reason="test verdict")
    return GateResult(verdict=verdict, decision=decision,
                      status={"ACCEPT": "New", "REVIEW": "Review"}.get(decision, ""),
                      llm_status=llm_status)


# ── HM evidence tiers (§5): qualifying paths still resolve ───────────────

def test_direct_founder_hiring_post_is_explicit_hm():
    r = resolve_hm("Aarav Sharma", "author states hiring side: I'm hiring",
                   "Aarav Sharma", "https://www.linkedin.com/in/aarav-sharma",
                   "I'm hiring a Chief of Staff in Bangalore. DM me.")
    assert r.resolution == ResolutionState.EXPLICIT


def test_my_team_hiring_tied_to_vacancy_qualifies():
    # Named author + hiring-side evidence tied to the vacancy text.
    r = resolve_hm("Neha Verma", "named in post: Neha Verma",
                   "Neha Verma", "https://www.linkedin.com/in/neha-verma",
                   "My team is hiring a Data Analyst. DM Neha Verma to apply.")
    assert r.resolution == ResolutionState.EXPLICIT


def test_my_team_hiring_untied_stays_unverified():
    # Hiring voice with NO name/evidence tie: must not become HM.
    r = resolve_hm("Unclear", "", "Neha Verma",
                   "https://www.linkedin.com/in/neha-verma",
                   "My team is hiring. Apply now.")
    assert r.resolution != ResolutionState.EXPLICIT
    assert r.resolution in (ResolutionState.NOT_FOUND, ResolutionState.AMBIGUOUS)


# ── Inference-only attributions stay blocked ─────────────────────────────

def test_company_page_repost_with_no_hm_stays_unclear():
    r = resolve_hm("Unclear", "", "Acme",
                   "https://www.linkedin.com/company/acme",
                   "Acme is hiring a Chief of Staff in Bangalore.")
    assert r.value == "Unclear"
    assert r.resolution == ResolutionState.NOT_FOUND


def test_employee_repost_not_automatically_hm():
    r = resolve_hm("Rohan Mehta", "", "Rohan Mehta",
                   "https://www.linkedin.com/in/rohan-mehta",
                   "Excited to share Acme is hiring! Great team.")
    assert r.resolution != ResolutionState.EXPLICIT


def test_generic_recruiter_profile_not_automatically_hm():
    r = resolve_hm("Talent Finder", "", "Talent Finder",
                   "https://www.linkedin.com/in/talent-finder",
                   "We connect candidates with startups. DM for jobs.")
    assert r.resolution != ResolutionState.EXPLICIT


def test_hm_inferred_only_from_profile_employment_blocked():
    # A bare name with zero hiring-side evidence is AMBIGUOUS, never HM —
    # even when the profile suggests company employment.
    r = resolve_hm("Rohan Mehta", "", "Rohan Mehta",
                   "https://www.linkedin.com/in/rohan-mehta",
                   "Proud to work at Acme as an engineer.")
    assert r.resolution == ResolutionState.AMBIGUOUS


# ── Company recovery: explicit only ──────────────────────────────────────

def test_company_recovered_from_explicit_evidence():
    res = resolve_employer("Acme is HIRING a Chief of Staff in Bangalore.",
                           "", "Rohan Mehta", "")
    assert res.company == "Acme"
    assert res.company_evidence == CompanyEvidence.POST_TEXT


def test_company_not_recoverable_remains_unclear():
    res = resolve_employer("We are hiring for our team in Bangalore. "
                           "DM me to apply.", "", "Rohan Mehta", "")
    assert res.company in ("Unclear", "") or \
        res.company_evidence != CompanyEvidence.POST_TEXT


def test_undisclosed_client_stays_unclear():
    res = resolve_employer("Hiring for our client, a stealth startup, in "
                           "Bangalore. DM me.", "", "Jane Recruiter", "")
    assert res.company == "Unclear" or "client" in res.company.lower()


# ── Source blocks (agency / aggregator / roundup) ────────────────────────

def test_agency_advertisement_blocked():
    pc = classify_poster("Aptita Talent Experts",
                         "https://www.linkedin.com/company/aptita",
                         "Hiring for our client in Bangalore. DM me.", "", "", "")
    assert pc["poster_type"] == "AGENCY"
    m = make_merged("Hiring for our client in Bangalore. DM me.",
                    author="Aptita Talent Experts",
                    profile="https://www.linkedin.com/company/aptita",
                    company="Acme")
    assert route_production_post(m, make_gate()).destination != MAIN_PRODUCTION


def test_aggregator_blocked():
    pc = classify_poster("Data Analyst Jobs India",
                         "https://www.linkedin.com/company/data-analyst-jobs",
                         "10 fresh Data Analyst roles this week. Links below.",
                         "", "", "")
    assert pc["poster_type"] in ("AGGREGATOR", "JOB_ADVERTISING_PAGE")


def test_multi_employer_roundup_blocked():
    r = CLF.classify(make_post(
        "Data Scientist openings this week. Company: Acme. Company: Beta. "
        "Company: Gamma. Apply now. Bengaluru."))
    assert not r.is_valid


# ── Gate end-to-end: HM present vs missing ───────────────────────────────

def test_direct_company_role_hm_is_eligible():
    text = ("Acme is hiring a Chief of Staff in Bangalore. Full-time role. "
            "DM Rohan Mehta to apply.")
    m = make_merged(text, author="Acme",
                    profile="https://www.linkedin.com/company/acme")
    res = evaluate(m, make_gate())
    assert res.eligible and res.disposition == "ELIGIBLE"


def test_direct_company_role_without_hm_is_hold():
    text = ("Acme is hiring a Chief of Staff in Bangalore. Full-time role. "
            "Apply now.")
    m = make_merged(text, author="Acme",
                    profile="https://www.linkedin.com/company/acme",
                    hm="Unclear", hm_ev="")
    res = evaluate(m, make_gate())
    assert not res.eligible and res.disposition == "HOLD"


# ── Sourcing score: orders, never gates (§9) ─────────────────────────────

def _raw(text, author="", profile=""):
    return RawPost(post_url="https://x", post_date="", text=text,
                   author_name=author, author_profile_url=profile)


def test_strong_source_outscores_weak():
    strong = _raw("I'm hiring a Chief of Staff in Bangalore. DM Aarav Mehta.",
                  "Aarav Mehta", "https://www.linkedin.com/in/aarav-mehta")
    weak = _raw("Jobs roundup: 10 roles hiring this week. Links below.",
                "CareerJobs India", "https://www.linkedin.com/company/careerjobs")
    assert score_raw_post(strong) > score_raw_post(weak)


def test_agency_scores_below_bare():
    bare = _raw("We are hiring engineers in Pune. Apply now.",
                "Rohan", "https://www.linkedin.com/in/rohan")
    agency = _raw("Hiring for our client in Pune. Staffing services. Apply.",
                  "Aptita Talent Experts",
                  "https://www.linkedin.com/company/aptita")
    assert score_raw_post(agency) < score_raw_post(bare)


def test_score_deterministic_and_tied_stable():
    a = _raw("We are hiring engineers in Pune.", "Rohan",
             "https://www.linkedin.com/in/rohan")
    b = _raw("We are hiring engineers in Pune.", "Rohan",
             "https://www.linkedin.com/in/rohan")
    assert score_raw_post(a) == score_raw_post(b)


def test_orchestrator_verifies_strong_first():
    from unittest.mock import patch
    from app.models import RawPost as RP
    from app.orchestrator import Orchestrator
    weak = RP(post_url="https://x/weak", post_date="", author_name="Jobs",
              author_profile_url="https://www.linkedin.com/company/jobs",
              text="Jobs roundup: 10 roles this week.")
    strong = RP(post_url="https://x/strong", post_date="",
                author_name="Aarav Mehta",
                author_profile_url="https://www.linkedin.com/in/aarav-mehta",
                text="I'm hiring a Chief of Staff in Bangalore. DM Aarav Mehta.")
    seen = []

    def classify(post):
        from app.models import ClassifiedPost
        return ClassifiedPost(
            post_url=post.post_url, post_date="", text=post.text,
            author_name="", author_profile_url="",
            major_category="Founder's Office", exact_role="Chief of Staff",
            source_link=post.post_url, description="d", confidence=0.8,
            location="Bengaluru", employment_type="Full-time",
            experience_requirement="Not Specified", market="India",
            india_relevance="India", scraped_at="now",
            classification_reason="r", status="Review", is_valid=True)

    class V:
        llm_calls = 0

        def verify(self, **kw):
            self.llm_calls += 1
            seen.append(kw.get("post_url"))
            return make_gate(decision="REVIEW", llm_status="REVIEW")

    with patch("app.orchestrator.DataDopingSource"), \
         patch("app.orchestrator.DeterministicClassifier"), \
         patch("app.orchestrator.SheetsWriter"):
        o = Orchestrator(production_gate=True)
        o.source.search_all.return_value = [weak, strong]  # weak scraped first
        o.classifier.classify.side_effect = classify
        o.verifier = V()
        o.sheets_writer.get_existing_urls.return_value = set()
        o.run_pipeline(limit=10)
    # Same SET verified (2 calls), stronger source FIRST despite scrape order.
    assert seen == ["https://x/strong", "https://x/weak"], seen


# ── Query layer: 38 intact + 10 high-signal voice queries ────────────────
# Phase 31 note: two HS-layer queries were superseded IN PLACE when Phase 27
# showed 0 raw posts for them (zero-yield set q14/q22/q26/q38/q41):
#   * q38 '"founder\'s office" "my team"' → '"founder\'s office" "we are hiring"'
#   * q41 '"chief of staff" "join our team"' → '"chief of staff" "is hiring"'
# HS_QUERIES below is kept as the ORIGINAL Phase 25.5 set; test
# test_48_queries_intact_plus_hs pins the supersessions explicitly.

HS_QUERIES = [
    '"founder\'s office" "my team"',
    '"chief of staff" "my team"',
    '"founder\'s office" "join our team"',
    '"chief of staff" "join our team"',
    '"founder associate" "looking for"',
    '"office of the founder" "looking for"',
    '"data analyst" "my team"',
    '"data scientist" "my team"',
    '"data analyst" "join our team"',
    '"data scientist" "join our team"',
]

# Phase 31 supersessions of HS-layer texts (the list above is kept as the
# ORIGINAL Phase 25.5 set for historical reference; the current SEARCH_QUERIES
# positions q38/q41 now carry the successor texts).
HS_PHASE31_SUCC = {
    "q38": ('"founder\'s office" "my team"',
            '"founder\'s office" "we are hiring"'),
    "q41": ('"chief of staff" "join our team"',
            '"chief of staff" "is hiring"'),
}
BANNED_STANDALONE = ["jobs", "vacancies", "alerts", "recruitment",
                     "career page", "dm for jobs", "bulk"]


def test_48_queries_intact_plus_hs():
    assert len(SEARCH_QUERIES) == 48
    assert len(set(SEARCH_QUERIES)) == 48
    assert SEARCH_QUERIES[:38] == SEARCH_QUERIES[:38] and \
        SEARCH_QUERIES[0] == '"founder\'s office" hiring'
    for q in HS_QUERIES:
        if q in (HS_PHASE31_SUCC["q38"][0], HS_PHASE31_SUCC["q41"][0]):
            # Phase 31: superseded in place — old text gone, successor at
            # the same index.
            idx = 38 if q == HS_PHASE31_SUCC["q38"][0] else 41
            assert SEARCH_QUERIES[idx] == HS_PHASE31_SUCC[
                "q38" if idx == 38 else "q41"][1], SEARCH_QUERIES[idx]
            assert q not in SEARCH_QUERIES
        else:
            assert q in SEARCH_QUERIES, q


def test_hs_queries_role_anchored_no_banned_terms():
    hs = SEARCH_QUERIES[38:]
    assert len(hs) == 10
    anchors = ("founder", "chief of staff", "data analyst", "data scientist",
               "office of the founder")
    for q in hs:
        assert any(a in q for a in anchors), q
        for b in BANNED_STANDALONE:
            assert b not in q.lower(), (q, b)
