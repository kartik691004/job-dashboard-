"""Phase 31 — sourcing query precision update: offline dry-run tests.

Pins (a) the Phase 27 higher-signal query families byte-identical, (b) the
8 evidence-based replacements at their exact indices, (c) sourcing invariants
(no banned broad terms, role-anchored, unique for attribution), and (d) the
scraper's verbatim keyword passthrough (no network: DataDopingSource._collect
is patched). No Apify, no LLM, no Sheets.
"""
from __future__ import annotations

import pytest

from app.config import SEARCH_QUERIES

# Phase 27 higher-signal families — MUST remain byte-identical (spec §1).
PROVEN_FAMILIES = {
    0: '"founder\'s office" hiring',
    2: '"founder\'s office" apply',
    33: '"data scientist" hiring',
    34: '"data scientist" apply',
    46: '"data analyst" "join our team"',
}
KEPT_OTHERS = {
    13: '"founder associate" apply',
    21: '"founder\'s office" growth',
    25: '"chief of staff" operations',
    29: '"data analyst" apply',
    37: '"applied scientist" hiring',
    40: '"founder\'s office" "join our team"',
}

# Phase 31 replacements — exact index == exact new text.
REPLACEMENTS = {
    14: '"chief of staff" "we are hiring"',      # was "office of the founder" apply (0 raw)
    22: '"founder\'s office" "is hiring"',       # was "fellow" (0 raw)
    26: '"chief of staff" Mumbai',               # was Bangalore (0 raw)
    30: '"data analyst" "we are hiring"',        # was "data analyst" India (ad-magnet)
    31: '"business intelligence analyst" "join our team"',  # was role+hiring (ad-magnet)
    35: '"data scientist" "we are hiring"',      # was "data scientist" India (ad-magnet)
    38: '"founder\'s office" "we are hiring"',   # was "my team" (0 raw)
    41: '"chief of staff" "is hiring"',          # was "join our team" (0 raw)
}

BANNED_TERMS = (
    "jobs", "vacanc", "alerts", "recruitment", "careers", "career",
    "naukri", "freshersworld", "iimjobs", "cutshort", "foundit",
    "referral", "job search", "hiring hub", "job hub", "talent hub",
)

ROLE_ANCHORS = (
    "founder's office", "chief of staff", "founder associate",
    "founder's associate", "office of the founder", "founder's team",
    "office of the ceo", "ceo's office", "data analyst",
    "business intelligence analyst", "product analyst", "data scientist",
    "data science", "applied scientist",
)


def test_query_count_unchanged():
    assert len(SEARCH_QUERIES) == 48


@pytest.mark.parametrize("idx,expected", sorted(PROVEN_FAMILIES.items()))
def test_proven_families_byte_identical(idx, expected):
    assert SEARCH_QUERIES[idx] == expected


@pytest.mark.parametrize("idx,expected", sorted(KEPT_OTHERS.items()))
def test_kept_queries_unchanged(idx, expected):
    assert SEARCH_QUERIES[idx] == expected


@pytest.mark.parametrize("idx,expected", sorted(REPLACEMENTS.items()))
def test_replacements_at_exact_indices(idx, expected):
    assert SEARCH_QUERIES[idx] == expected


def test_replaced_old_texts_gone():
    old = {
        '"data analyst" India',
        '"data scientist" India',
        '"business intelligence analyst" hiring',
        '"office of the founder" apply',
        '"founder\'s office" fellow',
        '"chief of staff" Bangalore',
        '"founder\'s office" "my team"',
        '"chief of staff" "join our team"',
    }
    assert not (old & set(SEARCH_QUERIES))


def test_no_banned_broad_terms():
    hits = [(i, q, b) for i, q in enumerate(SEARCH_QUERIES)
            for b in BANNED_TERMS if b in q.lower()]
    assert hits == []


def test_every_query_role_anchored():
    unanchored = [q for q in SEARCH_QUERIES
                  if not any(a in q.lower() for a in ROLE_ANCHORS)]
    assert unanchored == []


def test_queries_unique_for_attribution():
    assert len(SEARCH_QUERIES) == len(set(SEARCH_QUERIES))


def test_voice_never_alone():
    # Every hiring-voice phrase rides with a quoted role anchor in the same query.
    voice = ("we are hiring", "is hiring", "join our team", "my team", "looking for")
    for q in SEARCH_QUERIES:
        if any(v in q.lower() for v in voice):
            assert any(a in q.lower() for a in ROLE_ANCHORS), q


def test_scraper_passthrough_verbatim(monkeypatch):
    """search_all must forward SEARCH_QUERIES unchanged (strip-only)."""
    from app.sources import datadoping_source as dds

    captured = {}

    def fake_collect(self, payload):
        captured.update(payload)
        return []

    monkeypatch.setattr(dds.DataDopingSource, "_collect", fake_collect)
    src = dds.DataDopingSource("phase31-dry-run-no-network")
    src.search_all(SEARCH_QUERIES, max_posts=10)
    assert captured["keywords"] == list(SEARCH_QUERIES)
    assert captured["max_posts"] == 10
    assert captured["sort_by"] == "date_posted"


def test_no_downstream_pipeline_module_consumes_queries_differently():
    """Gate/classifier/verifier never read SEARCH_QUERIES — config-only surface."""
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, "-c",
         "import app.config as c; print(len(c.SEARCH_QUERIES))"],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "48"
