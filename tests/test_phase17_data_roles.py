"""
test_phase17_data_roles.py — Phase 17 Data Analyst / Data Scientist categories.

Deterministic only (no Apify/Groq/Sheets). Each test pins ONE Phase-17
behavior against a synthetic post. The frozen baseline (555 passed /
1 skipped) must stay green: FO/CoS detection order + values are pinned, the
23-column schema is untouched, and every pre-existing quality gate still runs
first (aggregator / repost / candidate / international / scam / internship /
company safeguards all apply equally to data leads).

Same pipeline, two new explicit lead categories: "Data Analyst" and
"Data Scientist" in the existing Major Category column (no migration).
"""
import sys
from pathlib import Path

import pytest

@pytest.fixture(autouse=True)
def allow_ungated(monkeypatch):
    monkeypatch.setattr("app.config.ALLOW_UNGATED_PRODUCTION_WRITE", True, raising=False)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import extraction as E
from app.classifier import (
    DeterministicClassifier,
    _detect_data_role,
    _has_data_actual_role_evidence,
    _has_data_role_context_only,
    _is_data_entry_vacancy,
)
from app.config import (
    BUSINESS_ANALYST_DATA_EVIDENCE_TERMS,
    DATA_ANALYST_KEYWORDS,
    DATA_FIELD_PHRASES,
    DATA_SCIENTIST_KEYWORDS,
    SEARCH_QUERIES,
)
from app.models import RawPost
from app.sheets_writer import EXPECTED_HEADERS

CLF = DeterministicClassifier()


def make_post(text, author="Aditi Rao",
              profile="https://linkedin.com/in/aditi-rao",
              card_loc="", card_co="Unclear"):
    return RawPost(
        post_url="https://www.linkedin.com/posts/test-activity-1",
        post_date="2026-09-01",
        text=text,
        author_name=author,
        author_profile_url=profile,
        company=card_co,
        job_card_location=card_loc,
        job_card_company=card_co,
        job_card_employment_type="Unclear",
        job_card_experience="Unclear",
    )


# ── 1. Data Analyst detection (brief ACCEPT examples) ───────────────────────

def test_da_looking_for_data_analyst_accepts():
    r = CLF.classify(make_post(
        "Looking for a Data Analyst in Bangalore. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Analyst"
    assert "data analyst" in r.exact_role.lower()


def test_da_founder_hiring_for_data_analyst_accepts():
    # "Founder hiring" is context; the advertised vacancy is the Data role.
    r = CLF.classify(make_post(
        "Founder hiring for a Data Analyst in Mumbai. Full-time. DM to apply."))
    assert r.is_valid
    assert r.major_category == "Data Analyst"


def test_da_title_dash_location_accepts():
    r = CLF.classify(make_post(
        "Data Analyst — Bangalore — Full Time. We are hiring. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Analyst"
    assert r.location == "Bengaluru"


def test_da_bi_analyst_variant():
    r = CLF.classify(make_post(
        "We are hiring a BI Analyst in Chennai. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Analyst"
    assert "bi analyst" in r.exact_role.lower()


def test_da_business_intelligence_variant():
    r = CLF.classify(make_post(
        "Hiring Business Intelligence Analyst in Hyderabad. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Analyst"


def test_da_reporting_analyst_variant():
    r = CLF.classify(make_post(
        "We are looking for a Reporting Analyst in Pune. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Analyst"


def test_da_product_analyst_variants():
    for title in ("Product Analyst", "Product Data Analyst"):
        r = CLF.classify(make_post(
            f"Looking for a {title} in Noida. Full-time. Apply now."))
        assert r.is_valid, title
        assert r.major_category == "Data Analyst", title


def test_da_growth_marketing_operations_variants():
    for title in ("Growth Data Analyst", "Marketing Data Analyst",
                  "Operations Data Analyst"):
        r = CLF.classify(make_post(
            f"We are hiring a {title} in Mumbai. Full-time. Apply now."))
        assert r.is_valid, title
        assert r.major_category == "Data Analyst", title


def test_da_analytics_associate_and_seniority_variants():
    for title in ("Analytics Associate", "Junior Data Analyst",
                  "Associate Data Analyst", "Senior Data Analyst",
                  "Business Data Analyst", "Analytics Analyst"):
        r = CLF.classify(make_post(
            f"Hiring {title} in Gurgaon. Full-time. Apply now."))
        assert r.is_valid, title
        assert r.major_category == "Data Analyst", title


# ── 2. Data Scientist detection ─────────────────────────────────────────────

def test_ds_basic_accepts():
    r = CLF.classify(make_post(
        "We are hiring a Data Scientist in Bengaluru. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Scientist"
    assert "data scientist" in r.exact_role.lower()


def test_ds_applied_product_research_variants():
    for title in ("Applied Data Scientist", "Product Data Scientist",
                  "Research Data Scientist",
                  "Machine Learning Data Scientist"):
        r = CLF.classify(make_post(
            f"Looking for a {title} in Mumbai. Full-time. Apply now."))
        assert r.is_valid, title
        assert r.major_category == "Data Scientist", title


def test_ds_ml_scientist_close_variants():
    # LinkedIn hiring language: ML Scientist / Machine Learning Scientist are
    # self-evidencing DS titles. Bare "Applied Scientist" is Phase-25.3
    # evidence-conditional (§4): it admits only with supporting DS/ML
    # evidence in the post.
    for title in ("ML Scientist", "Machine Learning Scientist"):
        r = CLF.classify(make_post(
            f"We are hiring an {title} in Hyderabad. Full-time. Apply now."))
        assert r.is_valid, title
        assert r.major_category == "Data Scientist", title
    r = CLF.classify(make_post(
        "We are hiring an Applied Scientist in Hyderabad to build machine "
        "learning models. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Scientist"


def test_bare_applied_scientist_without_evidence_stays_out():
    r = CLF.classify(make_post(
        "We are hiring an Applied Scientist in Hyderabad. Full-time. "
        "Apply now."))
    assert not r.is_valid


def test_ds_seniority_variants():
    for title in ("Junior Data Scientist", "Associate Data Scientist",
                  "Data Science Associate", "Senior Data Scientist"):
        r = CLF.classify(make_post(
            f"Hiring {title} in Pune. Full-time. Apply now."))
        assert r.is_valid, title
        assert r.major_category == "Data Scientist", title


# ── 3. Precision false positives (brief MUST-REJECT shapes) ─────────────────

def test_fp_founder_who_understands_data_rejected():
    r = CLF.classify(make_post(
        "Looking for a founder who understands data analytics in Mumbai. "
        "Full-time. Apply now."))
    assert not r.is_valid


def test_fp_swe_with_python_ml_rejected():
    r = CLF.classify(make_post(
        "Hiring Software Engineer with Python/ML experience in Pune. "
        "Full-time. Apply now."))
    assert not r.is_valid


def test_fp_marketing_role_requiring_analytics_rejected():
    r = CLF.classify(make_post(
        "Hiring for a marketing role requiring analytics in Delhi. "
        "Full-time. Apply now."))
    assert not r.is_valid


def test_fp_bare_skill_stack_rejected():
    # Python/SQL/dashboards/Excel are skills, never a vacancy by themselves.
    r = CLF.classify(make_post(
        "We are hiring engineers in Bangalore. Our stack uses Python, SQL, "
        "dashboards and Excel. Full-time. Apply now."))
    assert not r.is_valid


def test_fp_field_mention_without_vacancy_framing_rejected():
    r = CLF.classify(make_post(
        "We use data analytics across the company. Hiring engineers in "
        "Bangalore. Full-time. Apply now."))
    assert not r.is_valid
    r2 = CLF.classify(make_post(
        "Our data science team is growing. Hiring backend engineers in "
        "Mumbai. Full-time. Apply now."))
    assert not r2.is_valid


def test_fp_data_entry_operator_rejected():
    r = CLF.classify(make_post(
        "Hiring Data Entry Operator in Noida. Full-time. Apply now."))
    assert not r.is_valid


def test_fp_data_entry_framed_under_data_title_rejected():
    # Adversarial shape: a data title whose advertised work is data entry.
    r = CLF.classify(make_post(
        "Hiring Reporting Analyst for data entry operations in Delhi. "
        "Full-time. Apply now."))
    assert not r.is_valid
    assert "ata-entry" in r.classification_reason


def test_data_entry_vacancy_helper_shapes():
    assert _is_data_entry_vacancy("hiring data entry operator in noida")
    assert _is_data_entry_vacancy("role: data entry clerk in delhi")
    # Negated clarifications never count as the vacancy.
    assert not _is_data_entry_vacancy(
        "hiring data analyst in mumbai (not data entry work)")
    assert not _is_data_entry_vacancy("no data entry work involved")


def test_fp_negated_data_entry_clarification_still_accepts():
    r = CLF.classify(make_post(
        "Hiring Data Analyst in Mumbai (not data entry work). Full-time. "
        "Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Analyst"


# ── 4. Business Analyst ambiguity ───────────────────────────────────────────

def test_ba_generic_without_data_evidence_rejected():
    r = CLF.classify(make_post(
        "Hiring Business Analyst in Hyderabad. Client communication and "
        "documentation. Full-time. Apply now."))
    assert not r.is_valid


def test_ba_with_data_responsibilities_accepts_as_da():
    r = CLF.classify(make_post(
        "Hiring Business Analyst in Hyderabad. Must know SQL, Power BI and "
        "dashboards. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Analyst"


def test_ba_detection_requires_evidence_terms():
    cat, kw = _detect_data_role(
        "hiring business analyst in hyderabad. sql and dashboards required. "
        "full-time. apply now.",
        "Hiring Business Analyst in Hyderabad. SQL and dashboards required.",
    )
    assert (cat, kw) == ("Data Analyst", "business analyst")
    cat2, _ = _detect_data_role(
        "hiring business analyst in hyderabad. documentation. full-time.",
        "Hiring Business Analyst in Hyderabad. Documentation.",
    )
    assert cat2 == ""
    # "data"/"python" alone are not responsibility evidence (precision rule).
    assert "data" not in BUSINESS_ANALYST_DATA_EVIDENCE_TERMS


# ── 5. Keyword inventory pins ───────────────────────────────────────────────

def test_keyword_lists_cover_brief_titles():
    brief_da = ["data analyst", "analytics analyst", "business data analyst",
                "bi analyst", "business intelligence analyst",
                "reporting analyst", "product data analyst", "product analyst",
                "growth data analyst", "marketing data analyst",
                "operations data analyst", "analytics associate",
                "data analyst intern", "junior data analyst",
                "associate data analyst"]
    for kw in brief_da:
        assert kw in DATA_ANALYST_KEYWORDS, kw
    brief_ds = ["data scientist", "applied data scientist",
                "product data scientist",
                "machine learning data scientist", "ml scientist",
                "research data scientist", "data science associate",
                "junior data scientist", "associate data scientist",
                "data scientist intern"]
    for kw in brief_ds:
        assert kw in DATA_SCIENTIST_KEYWORDS, kw
    # "Data Analytics" / "Data Science" / "Data & Analytics" are FIELD
    # phrases (vacancy-framed only), never bare title keywords.
    assert "data analytics" in DATA_FIELD_PHRASES
    assert "data science" in DATA_FIELD_PHRASES
    assert "data analytics" not in DATA_ANALYST_KEYWORDS
    assert "data science" not in DATA_SCIENTIST_KEYWORDS


def test_actual_role_strong_and_weak_helpers():
    assert _has_data_actual_role_evidence(
        "We are hiring a Data Analyst in Mumbai. Apply now.")
    assert _has_data_actual_role_evidence("Role: Data Scientist, Bangalore.")
    assert not _has_data_actual_role_evidence(
        "Looking for a founder who understands data analytics.")
    assert _has_data_role_context_only(
        "looking for a founder who understands data analytics")
    assert _has_data_role_context_only(
        "hiring software engineer with python/ml experience")


# ── 6. Internship handling (same routing as FO/CoS) ─────────────────────────

def test_da_intern_passes_deterministic_for_llm_verdict():
    # Target-role interns proceed (Phase 15E recall fix); the LLM gate
    # rejects internships explicitly — never silently accepted.
    r = CLF.classify(make_post(
        "We are hiring a Data Analyst Intern in Bangalore. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Analyst"
    assert "internship" not in r.classification_reason.lower()


def test_ds_intern_passes_deterministic_for_llm_verdict():
    r = CLF.classify(make_post(
        "We are hiring a Data Scientist Intern in Mumbai. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Scientist"


def test_pure_internship_scrap_still_rejected():
    r = CLF.classify(make_post("Hiring interns for admin work in Bangalore."))
    assert not r.is_valid


def test_llm_gate_rejects_internship_employment():
    from app.llm.schemas import LeadVerdict
    from app.llm.verifier import Verifier

    class FakeProvider:
        def complete_json(self, system, user):
            return {"decision": "ACCEPT", "confidence": 0.95,
                    "is_current_job": True, "is_genuine_hiring": True,
                    "is_target_role": True, "major_category": "Data Analyst",
                    "actual_role": "Data Analyst Intern",
                    "india_relevance": "India",
                    "employment_type": "Internship"}

    gate = Verifier(FakeProvider()).verify(post_text="x")
    assert gate.decision == "REJECT"
    assert any("internship" in reason for reason in gate.reasons)


# ── 7. Hiring-manager extraction for data leads ─────────────────────────────

def test_da_im_hiring_attributes_person_author():
    r = CLF.classify(make_post(
        "I'm hiring a Data Analyst in Mumbai. Full-time. DM me."))
    assert r.is_valid
    assert r.hiring_manager_name == "Aditi Rao"
    assert "hiring" in r.hiring_manager_evidence


def test_ds_company_page_author_never_manager():
    r = CLF.classify(make_post(
        "We're hiring a Data Scientist in Pune. Full-time. Apply now.",
        author="Acme Corp",
        profile="https://www.linkedin.com/company/acme/posts"))
    assert r.is_valid
    assert r.hiring_manager_name == "Unclear"
    assert r.hiring_manager_evidence == ""


def test_da_named_cofounder_captured():
    r = CLF.classify(make_post(
        "We are hiring a Data Analyst in Delhi. You will work directly with "
        "me and my co-founder Rishi Jain. Full-time."))
    assert r.hiring_manager_name == "Rishi Jain"


# ── 8. Cold email / Google Form / apply link for data leads ─────────────────

def test_da_company_email_preserved():
    r = CLF.classify(make_post(
        "Hiring Data Analyst at Acme Robotics in Pune. Full-time. "
        "Send your resume to hiring@acmerobotics.in"))
    assert r.is_valid
    assert r.cold_email == "hiring@acmerobotics.in"


def test_ds_personal_email_gating_matches_policy():
    email, _ = E.choose_cold_email(
        "Data Scientist in Bangalore. Full-time. Contact: john.doe@gmail.com")
    assert email == "Not Available"
    email2, _ = E.choose_cold_email(
        "Data Scientist in Bangalore. Full-time. "
        "Send your resume to john.doe@gmail.com")
    assert email2 == "john.doe@gmail.com"


def test_da_google_form_only_in_form_column():
    r = CLF.classify(make_post(
        "We're Hiring Data Analyst in Mumbai. Full-time. "
        "Apply now: https://forms.gle/abcXYZ123"))
    assert r.is_valid
    assert r.apply_google_form == "https://forms.gle/abcXYZ123"
    assert r.apply_link == "https://forms.gle/abcXYZ123"
    assert "forms.gle" not in r.description
    assert "forms.gle" not in (r.key_points or "")


def test_ds_apply_link_shortlink_with_cue():
    r = CLF.classify(make_post(
        "Hiring Data Scientist in Chennai. Full-time. "
        "Apply here: https://lnkd.in/gKPT-R4D"))
    assert r.is_valid
    assert r.apply_link == "https://lnkd.in/gKPT-R4D"
    assert r.apply_google_form == ""


def test_key_points_never_contain_email():
    r = CLF.classify(make_post(
        "We're Hiring Data Analyst at Acme in Mumbai. Full-time. "
        "Send your resume to hiring@acmerobotics.in"))
    assert r.is_valid
    assert "hiring@acmerobotics.in" not in (r.key_points or "")
    assert "hiring@acmerobotics.in" not in r.description


# ── 9. Category assignment + FO/CoS conflict resolution ─────────────────────

def test_category_ds_post_is_ds_da_post_is_da():
    r_ds = CLF.classify(make_post(
        "We are hiring a Data Scientist in Bengaluru. Full-time. Apply now."))
    r_da = CLF.classify(make_post(
        "We are hiring a Data Analyst in Bengaluru. Full-time. Apply now."))
    assert r_ds.major_category == "Data Scientist"
    assert r_da.major_category == "Data Analyst"


def test_category_fo_cos_regression_pinned():
    r_fo = CLF.classify(make_post(
        "Looking for a Founder's Office Associate in Mumbai. Full-time. "
        "Apply now."))
    assert r_fo.is_valid
    assert r_fo.major_category == "Founder's Office"
    r_cos = CLF.classify(make_post(
        "We're hiring a Chief of Staff in Bangalore. Full-time. Apply now."))
    assert r_cos.is_valid
    assert r_cos.major_category == "Chief of Staff"


def test_conflict_genuine_fo_mentioning_data_team_stays_fo():
    r = CLF.classify(make_post(
        "Role: Founder's Office Associate. Work with the data analytics team "
        "in Mumbai. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Founder's Office"


def test_earliest_data_title_wins_between_da_and_ds():
    cat, kw = _detect_data_role(
        "hiring data scientist and data analyst in mumbai. full-time.",
        "Hiring Data Scientist and Data Analyst in Mumbai. Full-time.",
    )
    assert cat == "Data Scientist" and kw == "data scientist"


# ── 10. Company / location extraction for data leads ────────────────────────

def test_da_company_extraction_priority():
    r = CLF.classify(make_post(
        "Acme Robotics is hiring for a Data Analyst in Mumbai. Full-time. "
        "Apply now."))
    assert r.is_valid
    assert r.company_name == "Acme Robotics"


def test_ds_company_join_beats_client():
    r = CLF.classify(make_post(
        "We're Hiring Data Scientist. Join Enqurious, working with the Data "
        "and AI teams at Fractal in Bangalore. Full-time."))
    assert r.is_valid
    assert r.company_name == "Enqurious"


def test_da_location_city_and_card_backfill():
    r = CLF.classify(make_post(
        "Hiring Data Analyst in Jaipur. Full-time. Apply now."))
    assert r.location == "Jaipur"
    r2 = CLF.classify(make_post(
        "We're hiring a new Data Scientist. Full-time. Apply now.",
        card_loc="Indore, Madhya Pradesh, India (On-site)"))
    assert r2.is_valid
    assert r2.location == "Indore"


def test_ds_exact_role_multilevel_title():
    r = CLF.classify(make_post(
        "We are hiring a Senior Data Scientist in Mumbai. Full-time. "
        "Apply now."))
    assert "data scientist" in r.exact_role.lower()


# ── 11. Search query structure (measurable per category) ────────────────────

def test_search_queries_28_intact_plus_10_data():
    assert len(SEARCH_QUERIES) == 48
    assert len(set(SEARCH_QUERIES)) == len(SEARCH_QUERIES)
    # All 28 Phase-16 queries preserved byte-identical, in order.
    assert SEARCH_QUERIES[0] == '"founder\'s office" hiring'
    assert SEARCH_QUERIES[12] == '"founder\'s office" "LPA"'
    assert '"founder\'s team" hiring' in SEARCH_QUERIES
    assert '"chief of staff" chairperson' in SEARCH_QUERIES
    # Phase-17 queries, in order. Phase 31 replaced q30/q31/q35 IN PLACE
    # (ad-magnet queries: Phase 27 showed high candidate rate, ~0% HM-known,
    # same repost accounts); count and positions unchanged, proven broad
    # (hiring/apply) and precision (product/applied) anchors untouched.
    assert SEARCH_QUERIES[28:38] == [
        '"data analyst" hiring',
        '"data analyst" apply',
        '"data analyst" "we are hiring"',
        '"business intelligence analyst" "join our team"',
        '"product analyst" hiring',
        '"data scientist" hiring',
        '"data scientist" apply',
        '"data scientist" "we are hiring"',
        '"data science" hiring',
        '"applied scientist" hiring',
    ]
    # 10 Phase-17 queries: 5 DA + 5 DS, each with a hiring/apply/India
    # anchor so per-query yield/dup/candidate/ACCEPT-REVIEW-REJECT rates are
    # attributable without extra actor runs. (Phase 25.5 appends 10 more
    # hirer-voice queries after these; see test_phase25_5_sourcing.py.)
    new_queries = SEARCH_QUERIES[28:38]
    da_queries = [q for q in new_queries if "analyst" in q]
    ds_queries = [q for q in new_queries
                  if "scientist" in q or '"data science"' in q]
    assert len(da_queries) == 5, new_queries
    assert len(ds_queries) == 5, new_queries
    # Anchor policy after Phase 31: vacancy/CTA anchors (hiring/apply) or
    # employer-voice phrases observed in evidence-bearing posts — never
    # voice-alone, never banned broad terms.
    voice = ("we are hiring", "join our team", "is hiring")
    for q in new_queries:
        assert ("hiring" in q or "apply" in q or "India" in q
                or any(v in q for v in voice)), q
    assert '"data analyst" hiring' in SEARCH_QUERIES
    assert '"data analyst" apply' in SEARCH_QUERIES
    assert '"data analyst" "we are hiring"' in SEARCH_QUERIES
    assert '"data scientist" hiring' in SEARCH_QUERIES
    assert '"data scientist" apply' in SEARCH_QUERIES
    assert '"data scientist" "we are hiring"' in SEARCH_QUERIES
    # Geo India queries retired as ad-magnets; assert absence.
    assert '"data analyst" India' not in SEARCH_QUERIES
    assert '"data scientist" India' not in SEARCH_QUERIES


# ── 12. Schema impact: NO migration (category reuses Major Category) ────────

def test_schema_still_23_columns_category_reused():
    assert len(EXPECTED_HEADERS) == 23
    assert EXPECTED_HEADERS[1] == "Major Category"
    assert EXPECTED_HEADERS[7] == "Source Link"  # dedup column pinned
    assert EXPECTED_HEADERS[4] == "Cold Email"  # outreach column pinned


def test_sheets_row_carries_data_category_in_23_cols():
    from app.sheets_writer import SheetsWriter
    w = SheetsWriter.__new__(SheetsWriter)
    captured = {}

    class FakeWS:
        def append_rows(self, rows, value_input_option=None):
            captured["rows"] = rows
    w.dry_run = False
    w.worksheet = FakeWS()
    r = CLF.classify(make_post(
        "Looking for a Data Analyst in Bangalore. Full-time. Apply now."))
    assert r.is_valid
    w.write_posts([r])
    row = captured["rows"][0]
    assert len(row) == 23
    assert row[1] == "Data Analyst"
    r2 = CLF.classify(make_post(
        "We are hiring a Data Scientist in Mumbai. Full-time. Apply now."))
    w.write_posts([r2])
    assert captured["rows"][0][1] == "Data Scientist"


# ── 13. LLM gate + schema for data categories ───────────────────────────────

def test_llm_gate_accepts_da_and_ds_verdicts():
    from app.llm.verifier import Verifier

    class FakeProvider:
        def __init__(self, payload):
            self.payload = payload

        def complete_json(self, system, user):
            return dict(self.payload)

    def run_gate(category, role):
        return Verifier(FakeProvider({
            "decision": "ACCEPT", "confidence": 0.95,
            "is_current_job": True, "is_genuine_hiring": True,
            "is_target_role": True, "major_category": category,
            "actual_role": role, "exact_role": role, "company": "Acme",
            "india_relevance": "India", "location": "Mumbai",
            "employment_type": "Full-time", "reason": f"genuine {role}",
        })).verify(post_text=f"Hiring {role} in Mumbai. Apply now.")

    g_da = run_gate("Data Analyst", "Data Analyst")
    assert g_da.decision == "ACCEPT" and g_da.status == "New"
    g_ds = run_gate("Data Scientist", "Data Scientist")
    assert g_ds.decision == "ACCEPT" and g_ds.status == "New"


def test_llm_gate_rejects_wrong_role_with_data_aware_reason():
    from app.llm.verifier import Verifier

    class FakeProvider:
        def complete_json(self, system, user):
            return {"decision": "ACCEPT", "confidence": 0.95,
                    "is_current_job": True, "is_genuine_hiring": True,
                    "is_target_role": False, "major_category": "None",
                    "actual_role": "Software Engineer",
                    "india_relevance": "India",
                    "employment_type": "Full-time"}

    gate = Verifier(FakeProvider()).verify(post_text="x")
    assert gate.decision == "REJECT"
    assert any("Data Analyst" in reason and "Data Scientist" in reason
               for reason in gate.reasons)


def test_verdict_schema_accepts_new_categories_coerces_unknown():
    from app.llm.schemas import MAJOR_CATEGORIES, LeadVerdict
    assert {"Data Analyst", "Data Scientist"} <= MAJOR_CATEGORIES
    assert {"Founder's Office", "Chief of Staff", "None"} <= MAJOR_CATEGORIES
    assert LeadVerdict(major_category="Data Analyst").major_category == \
        "Data Analyst"
    assert LeadVerdict(major_category="Data Scientist").major_category == \
        "Data Scientist"
    assert LeadVerdict(major_category="Strategy").major_category == "None"


def test_enrichment_preserves_data_category():
    from app.enrichment import Enricher
    cp = CLF.classify(make_post(
        "Looking for a Data Analyst in Bangalore. Full-time. Apply now."))
    assert cp.is_valid
    enriched = Enricher().enrich_from_classified(cp, None)
    assert enriched.major_category == "Data Analyst"


def test_system_prompt_covers_data_precision_rules():
    from app.llm.verifier import SYSTEM_PROMPT
    assert "Data Analyst" in SYSTEM_PROMPT
    assert "Data Scientist" in SYSTEM_PROMPT
    assert "understands data analytics" in SYSTEM_PROMPT
    assert "Data entry" in SYSTEM_PROMPT
