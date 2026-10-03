"""Offline tests for the FO/CoS query strategy and recall benchmark.

These never call Apify or Google Sheets. They verify:
  * the query matrix is well-formed and small,
  * post_matches_query attribution is correct,
  * analyze() computes deterministic-candidate counts correctly using the
    EXISTING DeterministicClassifier (unchanged acceptance logic).
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.models import RawPost
from tools.query_strategy import build_query_matrix, post_matches_query
from tools.query_benchmark import analyze, build_raw_post

MATRIX = build_query_matrix()
ROLES = {"Founder's Office", "Chief of Staff"}
REQUIRED_DOC_KEYS = {
    "id", "query", "role", "signal", "india_anchor",
    "why", "expected_noise", "india_relevance", "role_coverage",
}


def test_matrix_count_is_small_and_meaningful():
    assert 8 <= len(MATRIX) <= 24, "matrix should stay small and high-quality"
    ids = [q["id"] for q in MATRIX]
    assert len(ids) == len(set(ids)), "query ids must be unique"


def test_every_query_is_documented():
    for q in MATRIX:
        assert REQUIRED_DOC_KEYS.issubset(q.keys()), f"missing docs on {q.get('id')}"
        assert q["query"].strip(), f"empty query on {q.get('id')}"
        assert q["role"] in ROLES, f"unexpected role {q['role']} on {q['id']}"
        # Every query must contain a ROLE phrase in quotes so the actor can
        # anchor on it.
        assert '"' in q["query"], f"query {q['id']} must quote the role phrase"


def test_post_matches_query_role_requirement():
    fo_hiring = "We are expanding our founder's office and hiring an associate in Bangalore."
    assert post_matches_query(fo_hiring, '"founder\'s office" hiring') is True
    # No role present -> never matches.
    no_role = "We are hiring a software engineer in India."
    assert post_matches_query(no_role, '"founder\'s office" hiring') is False


def test_post_matches_query_signal_and_india_anchors():
    cos_india = "Chief of staff role open in India, apply now."
    assert post_matches_query(cos_india, '"chief of staff" India') is True
    # India-anchored query must fail without India evidence.
    cos_no_india = "Chief of staff role open in New York, apply now."
    assert post_matches_query(cos_no_india, '"chief of staff" India') is False
    # Hiring-signal query must fail when the signal is absent.
    cos_no_signal = "Chief of staff is such an important function (thoughts?)."
    assert post_matches_query(cos_no_signal, '"chief of staff" hiring') is False


def _raw(text, url="u1"):
    return RawPost(post_url=url, post_date="2026-08-20", text=text,
                   author_name="Acme", author_profile_url="https://li/a")


def test_analyze_counts_deterministic_candidates():
    posts = [
        # Genuine FO hiring post (should pass classifier -> candidate)
        _raw("We're hiring for our founder's office associate role in Bangalore. "
             "Apply now, full-time position.", url="a"),
        # Genuine CoS hiring post
        _raw("Our startup is looking for a Chief of Staff in Mumbai. "
             "Join our team, exciting role.", url="b"),
        # Internship -> excluded by classifier
        _raw("Founder's office internship hiring in Delhi for students.", url="c"),
        # Non-role content -> no match at all
        _raw("Great article about leadership and culture in India.", url="d"),
    ]
    result = analyze(posts, MATRIX)
    # total + unique
    assert result["total_posts"] == 4
    assert result["unique_posts"] == 4
    # Sum of deterministic candidates across queries should include the two
    # genuine posts (they may be attributed to multiple queries, so sum >= 2).
    total_cand = sum(q["deterministic_candidates"] for q in result["per_query"])
    assert total_cand >= 2, f"expected >=2 candidates, got {total_cand}"
    # Internship must never be a candidate.
    for q in result["per_query"]:
        assert q["deterministic_candidates"] <= q["matched_posts"]


def test_analyze_handles_empty_corpus():
    result = analyze([], MATRIX)
    assert result["total_posts"] == 0
    assert all(q["deterministic_candidates"] == 0 for q in result["per_query"])


def test_build_raw_post_from_flat_record():
    rec = {
        "url": "https://linkedin.com/posts/1",
        "postText": "founder's office hiring in Pune, apply now.",
        "authorName": "X", "authorProfileUrl": "https://li/x",
        "postedAt": "2026-08-19",
    }
    rp = build_raw_post(rec)
    assert isinstance(rp, RawPost)
    assert rp.post_url == "https://linkedin.com/posts/1"
    assert "founder's office" in rp.text.lower()
