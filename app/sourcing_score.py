"""
sourcing_score.py — Phase 25.5 internal-only sourcing-quality score.

Prioritization + diagnosis ONLY. This score NEVER gates: it never promotes,
never rejects, never overrides production_gate, confidence, or routing. Its
sole production use is ordering deterministic candidates before LLM
verification (stable sort), so quota exhaustion degrades the weakest
candidates first. Unevaluated rows keep today's fail-closed REVIEW/HOLD fate.

Inputs are RawPost-level only (text + author), so ordering needs no LLM and
no classification. Pure + deterministic: identical posts score identically.
"""
from __future__ import annotations

import re

from app.config import AGGREGATOR_PATTERNS
from app.production_gate import _advertising_poster
from app.resolution import AGENCY_POSTER_PATTERNS, intermediary_evidence

# First-person hiring voice: the author personally owns the vacancy.
_SINGULAR_VOICE = (
    r"\bi(?:['\u2019]?m|\s+am)\s+hiring\b",
    r"\bmy\s+team\s+is\s+(?:hiring|looking\s+for)\b",
    r"\bhiring\s+for\s+my\s+team\b",
    r"\bjoin\s+my\s+team\b",
    r"\bcontact\s+me\s+(?:to\s+)?apply\b",
    r"\bdm\s+(?:me\s+)?[A-Z][a-z]+\b",
)
# Plural/company voice: hiring signal, weaker attribution.
_PLURAL_VOICE = (
    r"\bwe(?:['\u2019]?re|\s+are)\s+hiring\b",
    r"\bwe\s+are\s+looking\s+for\b",
    r"\bjoin\s+our\s+team\b",
)
_NAMED_CUE = re.compile(
    r"\bdm\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b|"
    r"\bcontact\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b",
    re.IGNORECASE,
)


def _is_person_profile(url: str) -> bool:
    u = (url or "").lower()
    return "/in/" in u and "/company/" not in u


def _is_company_page(url: str) -> bool:
    return "/company/" in (url or "").lower()


def _agency_shaped(author_name: str, text: str) -> bool:
    if any(re.search(p, author_name or "", re.IGNORECASE)
           for p in AGENCY_POSTER_PATTERNS):
        return True
    hit = intermediary_evidence(text or "", "")
    return bool(hit and "client" in hit.lower())


def score_raw_post(post) -> int:
    """Small integer signal score for one RawPost (higher = stronger source).

    Positive: +2 singular hiring voice, +1 plural voice, +1 person author,
    +1 company-page author, +1 named DM/contact cue.
    Negative: -3 aggregator-shaped text, -3 ad-shaped author,
    -2 agency-shaped. Floor/ceiling unbounded by design; ordering only.
    """
    text = getattr(post, "text", "") or ""
    author = getattr(post, "author_name", "") or ""
    profile = getattr(post, "author_profile_url", "") or ""
    score = 0
    if any(re.search(p, text, re.IGNORECASE) for p in _SINGULAR_VOICE):
        score += 2
    elif any(re.search(p, text, re.IGNORECASE) for p in _PLURAL_VOICE):
        score += 1
    if _is_person_profile(profile):
        score += 1
    elif _is_company_page(profile):
        score += 1
    if _NAMED_CUE.search(text):
        score += 1
    if any(re.search(p, text, re.IGNORECASE) for p in AGGREGATOR_PATTERNS):
        score -= 3
    if _advertising_poster(author, profile):
        score -= 3
    if _agency_shaped(author, text):
        score -= 2
    return score
