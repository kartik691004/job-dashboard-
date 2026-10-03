"""
test_phase10_label_signals.py — Phase 10 acceptance fix regression tests.

Phase-10 acceptance bug fixed: the Stage-3 hiring-intent proximity gate required
a HIRING_SIGNALS word (hiring / looking for / opening / apply / ...) near the
role keyword, but the explicit VOCANCY LABELS ("Role:", "Position:", "Job Title:",
"Title:", "Opening:", "Vacancy:", "Designation:", "Join as") were not recognised
as hiring-intent evidence. Genuinely label-framed vacancies (e.g. "Position:
Chief of Staff ... Noida.") were therefore rejected at "-Distance: inf" before
the actual-role stage could confirm them.

The fix adds `VACANCY_LABEL_SIGNALS` (config.py) and merges them into the Stage-3
proximity scan (classifier.py). It ONLY lets an ALREADY-target-role post satisfy
the proximity hint — it never widens which post types pass, because Stage 2 still
rejects posts with no FO/CoS keyword, and Stages 3b/3c still reject aggregators,
multi-employer roundups, context-only posts, and non-target roles.

Covers:
  - each explicit label satisfies the hiring-intent gate with a valid FO/CoS role
  - label + valid role + India proceeds to later gates (single-employer, valid)
  - bare Role:/Position: with NO target FO/CoS role is NOT accepted
  - context-only FO/CoS still rejects (even with a nearby label)
  - aggregator / multi-employer posts containing labels still reject
  - internship, job-seeker, non-India still reject (no gate weakened)
"""
import pytest

from app.classifier import DeterministicClassifier
from app.models import RawPost

classifier = DeterministicClassifier()


def make_post(text: str, author_name="Tester", author_profile_url="https://www.linkedin.com/in/tester") -> RawPost:
    return RawPost(
        post_url="http://test",
        post_date="2026-08-11",
        text=text,
        author_name=author_name,
        author_profile_url=author_profile_url,
    )


# ── 1-8: each explicit label satisfies the hiring-intent gate ────────────────

def test_position_label_satisfies_gate():
    post = make_post("Position: Chief of Staff - India Country Manager's Office. Noida.")
    res = classifier.classify(post)
    assert res.is_valid is True
    assert res.major_category == "Chief of Staff"


def test_role_label_satisfies_gate():
    post = make_post("Role: Founder's Office Associate. Mumbai.")
    res = classifier.classify(post)
    assert res.is_valid is True
    assert res.major_category == "Founder's Office"


def test_job_title_label_satisfies_gate():
    post = make_post("Job Title: Founder's Office Associate at Medulance, Pune.")
    res = classifier.classify(post)
    assert res.is_valid is True
    assert res.major_category == "Founder's Office"


def test_title_label_satisfies_gate():
    post = make_post("Title: Founder's Office Associate. Bengaluru.")
    res = classifier.classify(post)
    assert res.is_valid is True


def test_opening_label_satisfies_gate():
    post = make_post("Opening: Founder's Team / Business Operations. Hyderabad.")
    res = classifier.classify(post)
    assert res.is_valid is True


def test_vacancy_label_satisfies_gate():
    post = make_post("Vacancy: Chief of Staff. Chennai.")
    res = classifier.classify(post)
    assert res.is_valid is True


def test_designation_label_satisfies_gate():
    post = make_post("Designation: Chief of Staff. Delhi.")
    res = classifier.classify(post)
    assert res.is_valid is True
    assert res.major_category == "Chief of Staff"


def test_join_as_label_satisfies_gate():
    post = make_post("Join as Chief of Staff at Amama Partners, Bengaluru.")
    res = classifier.classify(post)
    assert res.is_valid is True
    assert res.major_category == "Chief of Staff"


# ── 9: label + valid role + India proceeds to later gates / single-employer ──

def test_label_valid_role_india_proceeds_and_company_kept():
    post = make_post("Position: Associate - Founder's Office (Strategy & Partnerships) at Amama Partners LLP. Bengaluru.")
    res = classifier.classify(post)
    assert res.is_valid is True
    assert "Amama" in res.company_name
    assert res.hiring_manager_name == "Unclear"  # company account, not a person


# ── 10-11: bare labels with NO target FO/CoS role do NOT get accepted ────────

def test_bare_role_label_non_target_role_rejected():
    post = make_post("Role: Marketing Manager. 5 years experience. Mumbai.")
    assert classifier.classify(post).is_valid is False


def test_bare_position_label_non_target_role_rejected():
    post = make_post("Position: Product Manager at Acme. Bengaluru.")
    assert classifier.classify(post).is_valid is False


def test_bare_job_title_label_non_target_role_rejected():
    post = make_post("Job Title: Sales Manager. Pune.")
    assert classifier.classify(post).is_valid is False


def test_bare_designation_label_non_target_role_rejected():
    post = make_post("Designation: Software Engineer. Delhi.")
    assert classifier.classify(post).is_valid is False


# ── 12-13: context-only FO/CoS still rejects even with a nearby label ────────

def test_label_on_non_target_with_fo_context_still_rejects():
    post = make_post("Role: Marketing Manager. Join our Founder's Office team in Mumbai.")
    assert classifier.classify(post).is_valid is False


def test_position_non_target_with_founders_team_context_still_rejects():
    post = make_post("Position: Operations Analyst. We are a Founder's Team company in Bangalore.")
    assert classifier.classify(post).is_valid is False


# ── 14-15: aggregator / multi-employer posts containing labels still reject ──

def test_aggregator_roundup_with_labels_still_rejects():
    post = make_post(
        "Role: Founder's Office Associate at Qure.ai. Weekly roundup: Chief of Staff "
        "at FirstCry, Product at Flipkart. All in India."
    )
    res = classifier.classify(post)
    assert res.is_valid is False
    assert "Aggregator" in res.classification_reason or "roundup" in res.classification_reason


def test_multi_employer_listing_with_labels_still_rejects():
    # A genuine multi-role roundup post carrying a target FO keyword + an
    # explicit label + India must still be rejected as an aggregator/roundup —
    # the label fix must NOT bypass the multi-employer/aggregator gate.
    post = make_post(
        "Flipkart Minutes put up Assistant Manager, one of a batch of 40 roles I went "
        "through yesterday. Role: Founder's Office Associate at Qure.ai, position: "
        "Chief of Staff at FirstCry, GTM roles at Flipkart. Mumbai."
    )
    res = classifier.classify(post)
    assert res.is_valid is False
    assert "Aggregator" in res.classification_reason or "roundup" in res.classification_reason


# ── 16-19: no gate weakened (internship / job-seeker / non-India / aggregator) ─

def test_internship_still_rejects():
    # Phase 15E: "Founder's Office Intern" in context of a target role is
    # no longer deterministically rejected. It passes the deterministic gate
    # and proceeds to later gates.
    post = make_post("Role: Founder's Office Intern. Bangalore. 3 months.")
    result = classifier.classify(post)
    assert result.is_valid is True
    assert result.major_category == "Founder's Office"


def test_job_seeker_still_rejects():
    post = make_post("I'm looking for a Founder's Office Associate role in Mumbai. Open to work.")
    assert classifier.classify(post).is_valid is False


def test_non_india_still_rejects():
    post = make_post("Role: Chief of Staff at Acme, New York, USA.")
    assert classifier.classify(post).is_valid is False


def test_company_account_not_hiring_manager():
    post = make_post(
        "Position: Founder's Office Associate at Digitayal. Bangalore.",
        author_name="Digitayal",
        author_profile_url="https://www.linkedin.com/company/digitayal/posts",
    )
    res = classifier.classify(post)
    assert res.company_name != "N/A"
    assert res.hiring_manager_name == "Unclear"
    assert res.hiring_manager_linkedin == "Unclear"


# ── Multi-employer structural gate (Phase 10 follow-up fix) ───────────────────
# The structural multi-employer guard (_distinct_employers) was silently disabled
# because it was fed lowercased text while its regex keyed on upper-case company
# initials. These tests prove the guard now fires on genuinely subject-framed
# multi-employer roundups WITHOUT over-rejecting single-employer posts.

def test_subject_framed_multi_employer_rejects():
    # Three distinct companies each "is/are hiring" a FO/CoS-related role.
    post = make_post(
        "Qure.ai is hiring for its CEO's Office in Mumbai. FirstCry.com is hiring "
        "a Chief of Staff for Beauty. Flipkart Minutes is hiring a Founder's Office "
        "Analyst for Operations."
    )
    res = classifier.classify(post)
    assert res.is_valid is False
    assert "Multi-employer" in res.classification_reason


def test_adverb_hiring_multi_employer_rejects():
    # "Flipkart is ALSO hiring" — the adverb between "is" and "hiring" must not
    # hide the second employer.
    post = make_post(
        "Qure.ai is hiring a Founder's Office Associate. Role: Category Manager at "
        "FirstCry. Flipkart is also hiring for its CEO's Office team in Mumbai."
    )
    res = classifier.classify(post)
    assert res.is_valid is False
    assert "Multi-employer" in res.classification_reason


def test_label_framed_multi_employer_rejects():
    # Multi-employer roundup with labels; the structural subject gate catches it
    # even though a label is present.
    post = make_post(
        "Qure.ai is hiring Founder's Office at its CEO's Office in Mumbai. Position: "
        "Chief of Staff at FirstCry.com. Flipkart Minutes is hiring role: Founder's "
        "Office Analyst for Operations."
    )
    res = classifier.classify(post)
    assert res.is_valid is False
    assert "Multi-employer" in res.classification_reason


def test_single_employer_apply_link_not_over_rejected():
    # A genuine single-employer post naming an apply platform must NOT be counted
    # as a second employer (object prepositions are deliberately not counted).
    post = make_post(
        "Acme is hiring a Founder's Office Associate in Bangalore. Apply at LinkedIn."
    )
    res = classifier.classify(post)
    assert res.is_valid is True


def test_single_employer_apply_website_not_over_rejected():
    post = make_post(
        "Acme Ventures is recruiting a Chief of Staff in Mumbai. Apply on our website careers.acme.com."
    )
    res = classifier.classify(post)
    assert res.is_valid is True


def test_single_employer_for_company_not_over_rejected():
    post = make_post(
        "We are hiring a Chief of Staff for Acme Ventures to work with the CEO in Mumbai."
    )
    res = classifier.classify(post)
    assert res.is_valid is True

