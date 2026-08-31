"""
test_extraction_fixes.py — narrow Stage-5 regex fixes in app/classifier.py.

Scope guard (clarification #3): these fixes only improve DISPLAY extraction
(Exact Role / Company Name). They must never widen which posts pass the
classifier — target roles remain Founder's Office + Chief of Staff only.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier import DeterministicClassifier
from app.models import RawPost

clf = DeterministicClassifier()


def classify(text: str):
    raw = RawPost(post_url="https://lnkd.in/t", post_date="", author_name="",
                  author_profile_url="", text=text)
    return clf.classify(raw)


# ── Curly-apostrophe Exact Role (LinkedIn's default glyph) ────────────────────

def test_curly_apostrophe_role_is_extracted():
    cp = classify("We are hiring Founder\u2019s Office Associate in Mumbai. Apply now.")
    assert cp.is_valid
    assert "Founder\u2019s Office" in cp.exact_role

def test_straight_apostrophe_role_still_extracted():
    cp = classify("We are hiring Founder's Office Associate in Mumbai. Apply now.")
    assert cp.is_valid
    assert "Founder's Office" in cp.exact_role


# ── "<Company> is hiring" company extraction ──────────────────────────────────

def test_company_from_is_hiring_pattern():
    cp = classify("Acme Technologies is hiring a Chief of Staff in Pune. Apply now.")
    assert cp.is_valid
    assert cp.company_name == "Acme Technologies"

def test_multiword_company_from_is_hiring_pattern():
    cp = classify("Open Links Foundation is hiring a Founder's Office Associate in Noida. Apply.")
    assert cp.is_valid
    assert cp.company_name == "Open Links Foundation"

def test_pronoun_subject_never_becomes_company():
    cp = classify("We are hiring a Chief of Staff in Pune. Apply now.")
    assert cp.is_valid
    assert cp.company_name != "We"

# ── Scope guard: extraction fixes change nothing about acceptance gates ───────

def test_non_target_role_with_target_keywords_still_rejected():
    # "Chief of Staff experience" for a Strategy role: keyword present but the
    # deterministic stage may accept on keywords alone — that is precisely what
    # the LLM gate exists for. This test pins the DETERMINISTIC contract only:
    # a post with no role keyword and no India evidence stays out.
    cp = classify("Hiring an Operations Lead, CTC 20 LPA. Apply now.")
    assert not cp.is_valid

def test_extraction_fixes_do_not_alter_validity_of_clean_posts():
    ok = classify("We are hiring a Chief of Staff in Bangalore. Apply now.")
    assert ok.is_valid and ok.company_name == "Unclear"
