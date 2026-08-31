"""
test_phase9a_bugfixes.py — regression tests for the two Phase-9A false-negative
bug fixes:

BUG 1 — actual-role detector was too fragile (missing re.IGNORECASE and
        separator tolerance). It must recognize vacancy headers separated by
        space / ':' / '|' / '-' / '–' / '—' / markdown '*' and title-case, while
        STILL not matching role-context mentions.

BUG 2 — job-seeker exclusion was over-broad ("actively looking" matched employer
        hiring language). It must now target candidate/opportunity-seeking
        language, not employer-side hiring phrases.
"""
import re
import pytest

from app.classifier import (
    DeterministicClassifier,
    _has_actual_role_evidence,
    _has_role_context_only,
)
from app.models import RawPost

classifier = DeterministicClassifier()


def make_post(text, author_name="Tester", author_profile_url="http://profile") -> RawPost:
    return RawPost(
        post_url="http://test",
        post_date="2026-08-11",
        text=text,
        author_name=author_name,
        author_profile_url=author_profile_url,
    )


# ── BUG 1: separator / case tolerance (strong actual-role evidence) ──────────

SEPARATOR_VARIANTS = [
    "space", "colon", "pipe", "hyphen", "en-dash", "em-dash", "markdown-*",
]

@pytest.mark.parametrize("stem", [
    "We're Hiring | Chief of Staff",
    "We're Hiring: Chief of Staff",
    "We're Hiring - Chief of Staff",
    "We're Hiring \u2013 Chief of Staff",
    "We're Hiring \u2014 Chief of Staff",
    "Hiring: Founder's Office",
    "Hiring | Founder's Office",
    "Role:* Founder's Office",
    "Position: Chief of Staff",
    "Title: Founder's Office Associate",
    "We are hiring a Chief of Staff",
    "looking for a Chief of Staff to work with the CEO",
    "We're hiring a Founder's Office Associate at XYZ in Bengaluru",
])
def test_strong_actual_role_evidence_separator_and_case(stem):
    assert _has_actual_role_evidence(stem) is True, f"expected STRONG for {stem!r}"


# Context-only mentions must NOT be mistaken for vacancy evidence (Bug 1 guard).

@pytest.mark.parametrize("context", [
    "high-ownership Founder's Office role",
    "experience in Founder's Office",
    "Founder's Office mindset",
    "works with the Founder's Office",
    "Founder's Office experience preferred",
    "Business Operations in the Founder's Team",
    "hiring a Generalist to work in the Founder's Office",
])
def test_context_only_not_strong(context):
    assert _has_actual_role_evidence(context) is False, f"expected WEAK for {context!r}"


# Real affected cases (must NOT be rejected because of a fragile role detector).

def test_jyotsna_masih_header_not_rejected_for_separator():
    post = make_post(
        "We're Hiring | Chief of Staff \u2013 Founder's Office\n"
        "BCT Ventures is building an AI-native consumer brands platform. Bengaluru."
    )
    assert classifier.classify(post).is_valid is True


def test_avinash_kumar_role_label_not_rejected():
    post = make_post(
        "Hiring: Founder's Office | Noida | Amama Partners LLP\n"
        "Role:* Founder's Office. Bengaluru."
    )
    assert classifier.classify(post).is_valid is True


def test_marketing_manager_high_ownership_fo_still_rejected():
    # Bug 1 must not turn role-context into a vacancy.
    post = make_post(
        "WE'RE HIRING | Assistant Manager \u2013 Marketing (Market Intelligence & Brand) | "
        "Pune. This is a high-ownership Founder's Office role where you will work with "
        "the Founders. 6 years experience."
    )
    res = classifier.classify(post)
    assert res.is_valid is False


# ── BUG 2: job-seeker detection must target candidate language only ──────────

@pytest.mark.parametrize("candidate_text", [
    "I am actively looking for a Chief of Staff role in Bangalore.",
    "I'm looking for opportunities in Founder's Office.",
    "Open to work as Chief of Staff in Bangalore.",
    "Looking for a role in Founder's Office in Mumbai.",
])
def test_candidate_job_seeker_rejected(candidate_text):
    res = classifier.classify(make_post(candidate_text))
    assert res.is_valid is False, f"expected REJECT for candidate language {candidate_text!r}"


@pytest.mark.parametrize("employer_text", [
    # B2C: employer hiring language must survive the job-seeker gate.
    "We are actively looking for a Chief of Staff at Acme in Bangalore.",
    "TopDog Law is actively looking for a strategic partner and Chief of Staff in the US.",
    "We're hiring a Chief of Staff for our Bangalore office.",
    "Our company is looking for a head of strategy.",
    "We're seeking a Chief of Staff to join the leadership team in Mumbai.",
    "Chief of Staff - CEO Office, Gurgaon, 2-4 years, ₹18 LPA. We are looking for candidates.",
])
def test_employer_hiring_language_not_job_seeker(employer_text):
    res = classifier.classify(make_post(employer_text))
    # The only allowed rejection for these is a NON-job-seeker gate (e.g. India
    # relevance in the TopDog / US case) — it must NOT be the job-seeker gate.
    assert res.is_valid is True or "job-seeker" not in res.classification_reason.lower(), (
        f"employer language wrongly job-seeker-rejected: {employer_text!r}"
    )


def test_earlferguson_employer_not_job_seeker():
    post = make_post(
        "We're Hiring: Chief of Staff | TopDog Law | Remote\n"
        "TopDog Law is the fastest-growing personal injury law firm in the country, "
        "and we are actively looking for a strategic partner to sit alongside our "
        "President and help drive the business forward."
    )
    res = classifier.classify(post)
    # It is rejected only for India relevance (US firm), NOT as a job seeker.
    assert "job-seeker" not in res.classification_reason.lower()


# Existing job-seeker behavior must continue to hold.

def test_existing_open_to_work_still_rejected():
    post = make_post("Open to work as Chief of Staff in Bangalore.")
    res = classifier.classify(post)
    assert res.is_valid is False
    assert "job" in res.classification_reason.lower() or "exclusion" in res.classification_reason.lower()


# ── Stage-3 recruiter / first-person fix ─────────────────────────────────────
# "I am looking for a <role>" must NOT be rejected as a job seeker when the
# poster is a RECRUITER / EMPLOYER sourcing a candidate (role framed for an
# organisation), while genuine first-person candidate seeking must still reject.

def test_recruiter_first_person_hiring_not_rejected():
    post = make_post(
        "I am looking for a Chief of Staff for a fast-growing startup in Bangalore. DM me."
    )
    res = classifier.classify(post)
    assert res.is_valid is True
    assert "job" not in res.classification_reason.lower()


def test_recruiter_i_am_looking_for_our_team_not_rejected():
    post = make_post(
        "I am looking for a Founder's Office Associate to join our team in Bangalore."
    )
    res = classifier.classify(post)
    assert res.is_valid is True
    assert "job" not in res.classification_reason.lower()


def test_recruiter_with_bangalore_accepts():
    post = make_post(
        "I am looking for a Chief of Staff for a fast-growing startup in Bangalore."
    )
    assert classifier.classify(post).is_valid is True


def test_recruiter_we_are_looking_for_not_job_seeker():
    post = make_post("We are looking for a Chief of Staff. DM me.")
    res = classifier.classify(post)
    assert "job-seeker" not in res.classification_reason.lower()


def test_recruiter_dm_interested_in_opening_not_job_seeker():
    post = make_post("DM me if you're interested in the Chief of Staff opening.")
    res = classifier.classify(post)
    assert "job-seeker" not in res.classification_reason.lower()


# Genuine first-person candidate seeking must STILL reject.

@pytest.mark.parametrize("candidate", [
    "I am looking for a Chief of Staff role in Bangalore.",
    "I am looking for a Chief of Staff position.",
    "I am actively looking for opportunities.",
    "I am looking for a job in Founder's Office.",
    "I am looking for a Founder's Office position.",
])
def test_first_person_candidate_still_rejected(candidate):
    res = classifier.classify(make_post(candidate))
    assert res.is_valid is False, f"candidate language wrongly accepted: {candidate!r}"
    assert "job" in res.classification_reason.lower()


def test_candidate_with_dm_me_but_seeking_employment_rejected():
    # "DM me" alone must not override clear candidate seeking.
    post = make_post("I'm looking for opportunities in Founder's Office, DM me.")
    res = classifier.classify(post)
    assert res.is_valid is False
    assert "job" in res.classification_reason.lower()


def test_ambiguous_first_person_fails_closed():
    # Ambiguous ("DM me" with no employer indicator) must NOT auto-accept.
    post = make_post("I am looking for a Chief of Staff. DM me.")
    res = classifier.classify(post)
    assert res.is_valid is False

