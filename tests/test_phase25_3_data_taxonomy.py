"""
test_phase25_3_data_taxonomy.py — Phase 25.3 structured DATA_ROLE taxonomy.

Deterministic only (no Apify/LLM/Sheets). Pins the title-first taxonomy:
exact/variant DATA titles admit (still gated by the unchanged proximity +
actual-role + India + full-time + LLM path); adjacent non-data titles,
weak-evidence-only posts, and framed negative titles stay out; bare
"Applied Scientist" and ERP-flavoured BA need explicit evidence.

Unicode shapes reuse the Phase-20 fold (matching-only); stored text is
never altered by classification.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier import DeterministicClassifier, _detect_data_role
from app.config import (
    APPLIED_SCIENTIST_EVIDENCE_TERMS,
    STRONG_DATA_EVIDENCE_TERMS,
    WEAK_DATA_EVIDENCE_TERMS,
)
from app.models import RawPost

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


def da_post(title, extra=""):
    return make_post(
        f"We are hiring a {title} in Bengaluru. Full-time. {extra}Apply now.")


def ds_post(title, extra=""):
    return make_post(
        f"We are hiring a {title} in Hyderabad. Full-time. {extra}Apply now.")


# ── Positive: Data Analyst variants (§3) ─────────────────────────────────

@pytest.mark.parametrize("title", [
    "Data Analyst",
    "Senior Data Analyst",
    "Product Data Analyst",
    "Business Data Analyst",
    "BI Analyst",
    "Financial Data Analyst",
    "Risk Data Analyst",
    "Supply Chain Data Analyst",
    "Customer Data Analyst",
    "Revenue Data Analyst",
    "Commercial Data Analyst",
    "Data Analysis Analyst",
    "Data Analytics Specialist",
])
def test_253_da_title_variants_accept(title):
    r = CLF.classify(da_post(title))
    assert r.is_valid, title
    assert r.major_category == "Data Analyst", title


# ── Positive: Data Scientist variants (§4) ───────────────────────────────

@pytest.mark.parametrize("title", [
    "Data Scientist",
    "Senior Data Scientist",
    "Applied Data Scientist",
    "Decision Scientist",
    "Data Science Specialist",
    "Data Science Engineer",
    "Generative AI Data Scientist",
    "ML Data Scientist",
    "Data Scientist & ML Developer",
])
def test_253_ds_title_variants_accept(title):
    r = CLF.classify(ds_post(title))
    assert r.is_valid, title
    assert r.major_category == "Data Scientist", title


def test_253_applied_scientist_with_ml_evidence_accepts():
    r = CLF.classify(ds_post(
        "Applied Scientist", extra="You will build machine learning models. "))
    assert r.is_valid
    assert r.major_category == "Data Scientist"


# ── Negative: adjacent titles stay out (§7) ──────────────────────────────

@pytest.mark.parametrize("title", [
    "Data Engineer",
    "ML Engineer",
    "Software Engineer",
    "Business Analyst",
    "Financial Analyst",
    "Research Analyst",
    "Marketing Analyst",
    "Operations Analyst",
    "MIS Executive",
    "Data Entry",
    "Data Entry Operator",
])
def test_253_adjacent_titles_stay_out(title):
    r = CLF.classify(make_post(
        f"We are hiring a {title} in Bengaluru. Full-time. Apply now."))
    assert not r.is_valid, title


def test_253_hiring_data_analysts_ad_stays_out():
    r = CLF.classify(make_post(
        "Hiring Data Analysts! 50 fresh openings across startups this week. "
        "DM for the job list. Bengaluru."))
    assert not r.is_valid


def test_253_multi_company_ds_list_stays_out():
    r = CLF.classify(make_post(
        "Data Scientist openings this week. Company: Acme. Company: Beta. "
        "Company: Gamma. Apply now. Bengaluru."))
    assert not r.is_valid


def test_253_ds_engineer_post_with_field_mention_stays_out():
    # Framed negative title owns the vacancy: "data science team" cannot
    # promote a Data Engineer post via the field-phrase path.
    r = CLF.classify(make_post(
        "Role: Data Engineer. Join our data science team in Bengaluru. "
        "Full-time. Apply now."))
    assert not r.is_valid
    assert _detect_data_role(
        "role: data engineer. join our data science team in bengaluru. "
        "full-time. apply now.",
        "Role: Data Engineer. Join our data science team.") == ("", "")


def test_253_ba_erp_with_dashboard_stays_out():
    r = CLF.classify(make_post(
        "We are hiring a Business Analyst for ERP requirement gathering "
        "and stakeholder management in Mumbai. Dashboards included. "
        "Full-time. Apply now."))
    assert not r.is_valid


def test_253_ba_with_strong_evidence_accepts():
    r = CLF.classify(make_post(
        "We are hiring a Business Analyst for statistical analysis and "
        "predictive modeling in Mumbai. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Analyst"


# ── Boundary (§9/§10) ────────────────────────────────────────────────────

def test_253_analytics_engineer_stays_out():
    r = CLF.classify(ds_post("Analytics Engineer"))
    assert not r.is_valid


def test_253_strategy_and_data_routes_fo_by_precedence():
    # Both families strongly framed: existing FO-precedence holds (the LLM
    # verdict, preferred by final_category, can still resolve DS).
    r = CLF.classify(make_post(
        "We are hiring a Strategy & Data Associate for the Founder's Office "
        "in Mumbai. Full-time. Apply now."))
    assert r.is_valid


def test_253_founders_office_data_context_stays_fo():
    r = CLF.classify(make_post(
        "We are hiring for the Founder's Office in Mumbai. You will work "
        "with data and SQL reporting. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Founder's Office"


def test_253_ds_founders_office_dual_routes_on_evidence():
    # DS vacancy strongly framed, FO mere context → Data Scientist.
    r = CLF.classify(make_post(
        "Our founder is hiring a Data Scientist in Bengaluru. Full-time. "
        "Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Scientist"


def test_253_sql_only_analyst_context_stays_out():
    r = CLF.classify(make_post(
        "We are hiring a Marketing Analyst in Pune. SQL reporting skills "
        "needed. Full-time. Apply now."))
    assert not r.is_valid


def test_253_python_heavy_non_data_stays_out():
    r = CLF.classify(make_post(
        "We are hiring a Software Engineer in Bengaluru. Heavy Python and "
        "machine learning libraries. Full-time. Apply now."))
    assert not r.is_valid


def test_253_weak_terms_never_promote():
    # Every WEAK term alone with a non-data title: still out (§6).
    for weak in WEAK_DATA_EVIDENCE_TERMS:
        r = CLF.classify(make_post(
            f"We are hiring a Marketing Manager in Pune. {weak} work. "
            f"Full-time. Apply now."))
        assert not r.is_valid, weak


def test_253_strong_terms_documented_for_evidence_paths():
    assert "machine learning" in STRONG_DATA_EVIDENCE_TERMS
    assert "data science" in STRONG_DATA_EVIDENCE_TERMS
    assert "business intelligence" in STRONG_DATA_EVIDENCE_TERMS
    assert "data" not in STRONG_DATA_EVIDENCE_TERMS
    assert "analysis" not in STRONG_DATA_EVIDENCE_TERMS
    assert APPLIED_SCIENTIST_EVIDENCE_TERMS


# ── Unicode (§11: fold is matching-only) ─────────────────────────────────

def test_253_styled_da_title_accepts():
    r = CLF.classify(make_post(
        "We are hiring a 𝐃𝐚𝐭𝐚 𝐀𝐧𝐚𝐥𝐲𝐬𝐭 in Bengaluru. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Analyst"


def test_253_styled_ds_title_accepts():
    r = CLF.classify(make_post(
        "We are hiring a 𝐃𝐚𝐭𝐚 𝐒𝐜𝐢𝐞𝐧𝐭𝐢𝐬𝐭 in Hyderabad. Full-time. Apply now."))
    assert r.is_valid
    assert r.major_category == "Data Scientist"
