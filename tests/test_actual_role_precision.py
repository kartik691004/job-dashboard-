"""
test_actual_role_precision.py — Phase 9A regression tests.

CORE PRODUCT RULE: the classifier must decide whether the ADVERTISED VACANCY in
the post is a Founder's Office / Chief of Staff position at ONE identifiable
employer — NOT merely whether the post CONTAINS those keywords.

Covers:
  - actual-role validation (vacancy vs context/experience/preference)
  - single-employer / multi-employer / aggregator detection
  - company never selected from an aggregator
  - anonymous-company ambiguous roles never auto-accepted
  - hiring-manager person/company URL rules
  - genuine single-employer FO/CoS vacancies still accepted
"""
import pytest

from app.classifier import DeterministicClassifier
from app.models import RawPost

classifier = DeterministicClassifier()


def make_post(text: str, author_name="Tester", author_profile_url="http://profile") -> RawPost:
    return RawPost(
        post_url="http://test",
        post_date="2026-08-11",
        text=text,
        author_name=author_name,
        author_profile_url=author_profile_url,
    )


# ── 1–2 / 10–13: role-context / wrong-role posts must NOT be accepted ────────

def test_marketing_manager_mentioning_fo_is_rejected():
    post = make_post(
        "WE'RE HIRING | Assistant Manager – Marketing (Market Intelligence & Brand) | "
        "Pune. This is a high-ownership Founder's Office role where you will work "
        "closely with the Founders. 6 years experience."
    )
    assert classifier.classify(post).is_valid is False


def test_business_ops_with_founders_team_is_not_accepted():
    post = make_post(
        "Founder's Team / Business Operations | High-Growth B2B SaaS | Bangalore | "
        "50 LPA. We are looking for a professional to join the Founder's Team. "
        "5+ years of experience in Founder's Office / Strategy / Business Operations."
    )
    assert classifier.classify(post).is_valid is False


def test_anonymous_business_ops_not_auto_accepted():
    post = make_post(
        "We are looking for a high-calibre professional to join the Founder's Team "
        "of a well-funded, high-growth B2B SaaS company. 5+ years of experience in "
        "Founder's Office / Strategy / Business Operations. Bangalore."
    )
    assert classifier.classify(post).is_valid is False


def test_founders_office_experience_preferred_rejected():
    post = make_post(
        "We are hiring Product Managers in Bangalore. Founder's Office experience preferred."
    )
    assert classifier.classify(post).is_valid is False


def test_high_ownership_fo_role_context_rejected():
    post = make_post(
        "WE'RE HIRING | Assistant Manager – Marketing (Market Intelligence & Brand) | "
        "Pune. This is a high-ownership Founder's Office role."
    )
    assert classifier.classify(post).is_valid is False


def test_chief_of_staff_experience_preferred_rejected():
    post = make_post(
        "Looking for a Product Manager for our team in Mumbai. Chief of Staff experience "
        "preferred."
    )
    assert classifier.classify(post).is_valid is False


# ── 3–4 / 14–15: genuine FO / CoS vacancies still pass ───────────────────────

def test_founders_office_associate_title_is_candidate():
    post = make_post("We're hiring a Founder's Office Associate at XYZ in Bengaluru.")
    assert classifier.classify(post).is_valid is True


def test_chief_of_staff_title_is_candidate():
    post = make_post("XYZ is looking for a Chief of Staff to work with the CEO in Bangalore.")
    assert classifier.classify(post).is_valid is True


def test_genuine_single_employer_fo_still_passes():
    post = make_post("Hiring: Founder's Office Executive at Acme, Bangalore.")
    res = classifier.classify(post)
    assert res.is_valid is True
    assert res.major_category == "Founder's Office"


def test_genuine_single_employer_cos_still_passes():
    post = make_post("We're looking for a Chief of Staff to work directly with the CEO in Mumbai.")
    res = classifier.classify(post)
    assert res.is_valid is True
    assert res.major_category == "Chief of Staff"


# ── Phase-9E Bug 2: Founder's Team / Business Operations vacancy route ────────

def test_founders_team_explicit_role_label_passes():
    # Explicit "Role:" label establishes Founder's Team as the advertised vacancy.
    post = make_post(
        "Founder's Team / Business Operations | High-Growth B2B SaaS | Bangalore | Up to 50 LPA. "
        "We are looking for a high-calibre professional to join the Founder's Team. "
        "Role: Founder's Team / Business Operations. 5+ years of experience in "
        "Founder's Office / Business Operations. Apply to careers@acme.com. "
        "#Hiring #FoundersOffice #BusinessOperations"
    )
    res = classifier.classify(post)
    assert res.is_valid is True
    assert res.major_category == "Founder's Office"


def test_founders_team_position_label_passes():
    post = make_post(
        "Position: Founder's Team. We're hiring for our B2B SaaS in India, 45 LPA. Bangalore."
    )
    res = classifier.classify(post)
    assert res.is_valid is True
    assert res.major_category == "Founder's Office"


def test_business_ops_contextual_still_rejects():
    # "Business Operations" describing the work, not the vacancy, stays rejected.
    post = make_post(
        "We're hiring a Business Operations Analyst to support the Founder's Team at Acme, Bangalore."
    )
    assert classifier.classify(post).is_valid is False


def test_product_manager_with_founders_team_context_still_rejects():
    post = make_post(
        "Looking for a Product Manager with experience working with the Founder's Team. Bangalore."
    )
    assert classifier.classify(post).is_valid is False


# ── 5 / 16: one company, multiple roles — FO stays a candidate ───────────────

def test_single_employer_multi_role_fo_remains_candidate():
    post = make_post(
        "XYZ is hiring: 1. Founder's Office Associate 2. Sales Manager 3. Product Manager, "
        "all in Bangalore. Full-time."
    )
    res = classifier.classify(post)
    assert res.is_valid is True
    assert "XYZ" in res.company_name
    assert "Acme" not in res.company_name  # not pulled from an aggregator


def test_single_employer_multi_role_not_aggregator():
    # Must NOT be aggregator-rejected just because multiple roles are listed.
    post = make_post(
        "XYZ is hiring a Founder's Office Associate and a Product Manager in Bangalore. Full-time."
    )
    res = classifier.classify(post)
    assert res.is_valid is True
    assert "mult" not in res.classification_reason.lower()  # not aggregator reason


# ── 6–9: aggregators / multi-employer roundups → REJECT, no company leak ─────

def test_multiple_companies_in_one_roundup_rejected():
    post = make_post(
        "Qure.ai is hiring for its CEO's Office in Mumbai. FirstCry is hiring a "
        "Category Manager. Flipkart is hiring for Ops. All India-based."
    )
    assert classifier.classify(post).is_valid is False


def test_batch_of_40_roles_rejected():
    post = make_post(
        "Flipkart Minutes put up an Assistant Manager opening, one of a batch of 40 roles "
        "I went through yesterday: Founder's Office, Chief of Staff, GTM roles. "
        "Qure.ai is hiring for its CEO's Office in Mumbai."
    )
    assert classifier.classify(post).is_valid is False


def test_qureai_firstcry_flipkart_multi_employer_rejected():
    post = make_post(
        "Qure.ai is hiring for its CEO's Office in Mumbai. FirstCry.com is hiring a "
        "Category Manager for Beauty. Flipkart Minutes is hiring for Operations."
    )
    assert classifier.classify(post).is_valid is False


def test_company_inside_aggregator_never_becomes_employer():
    # Even if something passes role detection, a company named inside a roundup
    # must not be treated as THE employer. Here the roundup uses a Founder's
    # Office keyword but is clearly multi-company -> rejected, company N/A.
    post = make_post(
        "A Founder's Office role at Qure.ai, plus Chief of Staff roles at FirstCry and "
        "Flipkart. Qure.ai is hiring for its CEO's Office. 40 roles in today's roundup."
    )
    res = classifier.classify(post)
    assert res.is_valid is False
    assert res.company_name in ("N/A", "Unclear")  # never becomes Qure.ai


# ── 17–18: hiring-manager person/company URL rules ────────────────────────────

def test_company_linkedin_url_never_becomes_hiring_manager():
    post = make_post(
        "WE ARE HIRING a Founder's Office Associate at Digitayal in Bangalore.",
        author_name="Digitayal",
        author_profile_url="https://www.linkedin.com/company/digitayal/posts",
    )
    res = classifier.classify(post)
    assert res.company_name != "N/A"          # the post itself is still valid
    assert res.hiring_manager_name == "Unclear"
    assert res.hiring_manager_linkedin == "Unclear"


def test_personal_in_profile_with_evidence_becomes_hiring_manager():
    post = make_post(
        "I'm hiring a Chief of Staff at Acme in Bangalore. DM me.",
        author_name="Kartik R",
        author_profile_url="https://www.linkedin.com/in/kartik-r",
    )
    res = classifier.classify(post)
    assert res.hiring_manager_name == "Kartik R"
    assert res.hiring_manager_linkedin == "https://www.linkedin.com/in/kartik-r"
