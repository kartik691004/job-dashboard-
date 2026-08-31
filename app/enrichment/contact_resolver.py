"""
contact_resolver.py — hiring-manager resolution (spec §7) and LinkedIn URL
handling (spec §8).

Hiring-manager hierarchy (highest confidence wins):
  1. Explicit hiring manager named in the post.
  2. Person explicitly saying "I'm hiring" (first-person hiring evidence).
  3. Recruiter / talent person explicitly handling the role.
  4. Founder / leadership person explicitly responsible for hiring.
  5. Original poster ONLY when there is strong evidence they are responsible
     for this vacancy.

The post author is NEVER automatically the hiring manager. If evidence is
insufficient the result is Name="Unclear", LinkedIn="Not Available".

LinkedIn URLs are only ever returned when actually present in the evidence.
A URL is NEVER fabricated from a person's name and profile slugs are never
invented (spec §8).
"""
from __future__ import annotations

import re
from typing import Optional

from app.enrichment.schemas import (
    HiringManagerEvidence,
    HiringManagerResolution,
)

# First-person hiring evidence: the author is the hiring side.
_FIRST_PERSON_HIRING = re.compile(
    r"\b(?:i['\u2019]?m\s+(?:hiring|recruiting)|"
    r"i\s+am\s+(?:hiring|recruiting)|"
    r"we['\u2019]?re\s+(?:hiring|recruiting)|"
    r"my\s+team\s+is\s+hiring|"
    r"i['\u2019]?m\s+the\s+(?:founder|ceo|co-founder|head)\b|"
    r"i\s+handle\s+this\s+(?:role|hiring)|"
    r"contact\s+me\s+(?:to\s+)?apply|"
    r"reach\s+out\s+to\s+me\b)\b",
    re.IGNORECASE,
)

# Recruiter / talent persona markers in the text.
_RECRUITER_PERSONA = re.compile(
    r"\b(?:recruit(?:er|ing|ment)|talent\s+(?:acquisition|team|partner)|"
    r"hiring\s+manager|hr\s+manager|people\s+team|talent\s+team)\b",
    re.IGNORECASE,
)

# Founder / leadership persona markers.
# NOTE: deliberately avoids treating "Founder's Office" / "Founder's Associate"
# (role titles) as a leadership-persona signal.
_LEADERSHIP_PERSONA = re.compile(
    r"\b(?:"
    r"(?:the\s+)?(?:co-founder|cofounder|founder)\s+(?:of|and|at|&|,|$)|"
    r"\b(?:ceo|chief\s+executive|managing\s+director|md)\s+(?:of|at|,|&|$)|"
    r"(?:i['\u2019]?m\s+the\s+(?:founder|ceo|co-founder))\b|"
    r"\bhead\s+of\s+[a-z]"
    r")\b",
    re.IGNORECASE,
)

_LINKEDIN_URL = re.compile(
    r"https?://(?:www\.|[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_]{2,}",
    re.IGNORECASE,
)

# Any path under /company/ (e.g. .../company/acme/posts) is a COMPANY PAGE, not
# a person. Such a URL must never be surfaced as a person's LinkedIn (Bug B).
_LINKEDIN_COMPANY_PATH = re.compile(
    r"linkedin\.com/company/",
    re.IGNORECASE,
)


def _person_linkedin_url(candidate: str) -> str:
    """Return `candidate` only if it is a PERSON's LinkedIn URL. A company-page
    URL (contains '/company/'), a non-LinkedIn value, or "Not Available" all
    yield "Not Available" — a URL is only ever a person's profile (spec §8)."""
    if not candidate:
        return "Not Available"
    if candidate == "Not Available":
        return "Not Available"
    if _LINKEDIN_COMPANY_PATH.search(candidate):
        return "Not Available"
    if _LINKEDIN_URL.search(candidate):
        return candidate
    return "Not Available"

_EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

# "reach out to <Name>", "contact <Name>", "<Name>, our <role>"
_NAME_NEAR_CONTACT = re.compile(
    r"(?:reach(?:ing)?\s+out\s+to|contact|dm|message|email|speak\s+with)\s+"
    r"([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,2})",
    re.IGNORECASE,
)


def _name_snippet(text: str, name: str) -> str:
    if not text or not name:
        return ""
    idx = text.find(name)
    if idx < 0:
        return ""
    start = max(0, idx - 40)
    end = min(len(text), idx + len(name) + 60)
    return re.sub(r"\s+", " ", text[start:end]).strip()[:160]


def resolve(
    post_text: str,
    author_name: str = "",
    author_profile_url: str = "",
    llm_hiring_manager_name: str = "Unclear",
    llm_hiring_manager_linkedin: str = "Unclear",
    llm_application_method: str = "",
    provider_hint_name=None,
    provider_hint_linkedin: str = "Not Available",
    provider_hint_evidence_url: str = "",
) -> HiringManagerResolution:
    """Resolve the most relevant person to contact about the vacancy.

    Evidence tiers, in order. The LLM (verifier) may already have attributed a
    hiring manager from explicit evidence — honoured at the top only when it did
    NOT default to "Unclear".

    `provider_hint_name` / `provider_hint_linkedin` are OPTIONAL, evidence-backed
    public-contact hints from a Phase-7 ContactDiscoveryProvider. They are used
    only as a weak last-resort tier (never over explicit in-post / LLM evidence,
    never invented).

    Never assumes the author is the hiring manager; never fabricates a URL.
    """
    text = post_text or ""

    # 0. Explicit LinkedIn URL anywhere in the post is real evidence.
    url_in_text = _LINKEDIN_URL.search(text)

    # Tier 1 — explicit hiring manager already attributed by the LLM verifier
    # (it only sets this with hiring-side evidence like "I'm hiring"). Honoured
    # only if it is a real name and a real (non-fabricated) URL when provided.
    if llm_hiring_manager_name and llm_hiring_manager_name != "Unclear":
        url = "Not Available"
        if llm_hiring_manager_linkedin and llm_hiring_manager_linkedin != "Unclear":
            url = _person_linkedin_url(llm_hiring_manager_linkedin)
        elif url_in_text and _is_in_path_evidence(text, ":in/"):
            url = url_in_text.group(0)
        return HiringManagerResolution(
            name=llm_hiring_manager_name,
            linkedin_url=url,
            is_author=_name_matches(llm_hiring_manager_name, author_name),
            confidence=0.9 if url != "Not Available" else 0.8,
            evidence=HiringManagerEvidence.EXPLICIT_HIRING_MANAGER,
            evidence_snippet=_name_snippet(text, llm_hiring_manager_name) or "LLM attribution",
        )

    # Tier 2 — first-person hiring evidence: the poster is the hiring side.
    if _FIRST_PERSON_HIRING.search(text) and author_name:
        url = url_in_text.group(0) if url_in_text else "Not Available"
        return HiringManagerResolution(
            name=author_name,
            linkedin_url=url,
            is_author=True,
            confidence=0.85 if url != "Not Available" else 0.8,
            evidence=HiringManagerEvidence.POSTER_IS_HIRING,
            evidence_snippet="poster states they are hiring",
        )

    # Tier 3 — an explicit name tied to "reach out to / contact / DM".
    named = _NAME_NEAR_CONTACT.search(text)
    if named:
        name = named.group(1).strip()
        if len(name) >= 2 and _is_probably_person(name):
            url = url_in_text.group(0) if url_in_text else "Not Available"
            return HiringManagerResolution(
                name=name,
                linkedin_url=url,
                is_author=_name_matches(name, author_name),
                confidence=0.75,
                evidence=HiringManagerEvidence.EXPLICIT_HIRING_MANAGER,
                evidence_snippet=_name_snippet(text, name),
            )

    # Tier 4 — recruiter/talent persona plus author name on the hiring side.
    if _RECRUITER_PERSONA.search(text) and author_name:
        url = url_in_text.group(0) if url_in_text else "Not Available"
        return HiringManagerResolution(
            name=author_name,
            linkedin_url=url,
            is_author=True,
            confidence=0.70,
            evidence=HiringManagerEvidence.RECRUITER_HANDLING_ROLE,
            evidence_snippet="recruiter/talent persona handling the role",
        )

    # Tier 5 — founder/leadership persona plus author name on the hiring side.
    if _LEADERSHIP_PERSONA.search(text) and author_name:
        url = url_in_text.group(0) if url_in_text else "Not Available"
        return HiringManagerResolution(
            name=author_name,
            linkedin_url=url,
            is_author=True,
            confidence=0.65,
            evidence=HiringManagerEvidence.FOUNDER_LEADERSHIP,
            evidence_snippet="founder/leadership persona on the hiring side",
        )

    # Tier 6 — an evidence-backed public contact hint from a Phase-7/8
    # ContactDiscoveryProvider. Only used when the provider actually returned a
    # verified public contact; never a fabrication. Weak tier by design: real
    # in-post / LLM evidence still wins above it.
    if provider_hint_name and provider_hint_name != "Unclear":
        url = "Not Available"
        if provider_hint_linkedin and provider_hint_linkedin != "Not Available":
            url = _person_linkedin_url(provider_hint_linkedin)
        return HiringManagerResolution(
            name=provider_hint_name,
            linkedin_url=url,
            is_author=_name_matches(provider_hint_name, author_name),
            confidence=0.5,
            evidence=HiringManagerEvidence.AUTHOR_CONTEXT,
            evidence_snippet=f"contact-discovery provider evidence for {provider_hint_name}",
            evidence_url=provider_hint_evidence_url or "",
        )

    # The author alone (no hiring-side evidence) is NOT a hiring manager.
    return HiringManagerResolution(
        name="Unclear",
        linkedin_url="Not Available",
        is_author=False,
        confidence=0.0,
        evidence=HiringManagerEvidence.UNCLEAR,
        evidence_snippet="insufficient evidence to attribute a hiring manager",
    )


def _name_matches(candidate: str, author: str) -> bool:
    if not candidate or not author:
        return False
    def norm(s: str) -> str:
        return re.sub(r"[^a-z]", "", s.lower())
    nc, na = norm(candidate), norm(author)
    return bool(nc) and (nc in na or na in nc)


def _is_probably_person(name: str) -> bool:
    """A name near 'reach out to' should be a personal name, not a company or a
    stopword. Treated conservatively: multi-word capitalized personal-ish names
    pass, single generic words fail."""
    if len(name.split()) > 3:
        return False
    banned = {"me", "us", "them", "him", "her", "you", "apply", "email", "dm", "hr"}
    if name.lower() in banned:
        return False
    # Personal names are usually 2+ words (first + last); single words are kept
    # only if they look like a proper personal name (capitalized, alphabetic).
    if len(name.split()) == 1 and len(name) > 2 and name[0].isupper() and name.isalpha():
        return True
    return len(name.split()) >= 1 and name[0].isupper()


def _is_in_path_evidence(text: str, marker: str) -> bool:
    return marker in text
