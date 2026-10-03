"""
resolution.py — Phase 18 internal field-resolution states (INTERNAL ONLY).

Problem: "Unclear" is overloaded. It hides three very different situations —
the employer is intentionally undisclosed, extraction failed on explicit text,
or a third party posted someone else's vacancy — and it also appears when the
post genuinely contains nothing to resolve.

This module labels HOW each field value was obtained without changing any
pipeline decision or any Google Sheet column. Everything here is consumed by
the recovery/audit artifacts and tests only:

  ResolutionState  — generic six states for role/location/employment/
                     experience (18D).
  EmployerState    — the Unclear tri-state for company (18C): never collapse
                     EMPLOYER_UNDISCLOSED / EXTRACTION_UNRESOLVED /
                     INTERMEDIARY_CLIENT_UNDISCLOSED into generic "Unclear".
  HMState          — hiring-manager relationship states (18H).
  EmploymentState  — FULL_TIME / PART_TIME / INTERNSHIP / CONTRACT /
                     TEMPORARY / UNKNOWN (18G; UNKNOWN is not a rejection).

Fail-closed throughout: insufficient evidence keeps the field unresolved
rather than guessing, exactly like the deterministic extractors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


# ── States ───────────────────────────────────────────────────────────────────

class ResolutionState:
    EXPLICIT = "EXPLICIT"
    EXTRACTED = "EXTRACTED"
    INFERRED_WITH_STRONG_EVIDENCE = "INFERRED_WITH_STRONG_EVIDENCE"
    INTERMEDIARY_UNDISCLOSED = "INTERMEDIARY_UNDISCLOSED"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"


class EmployerState:
    """Company-specific resolution (18C). Only one may apply; when the value
    is a real company the state is EXPLICIT_EMPLOYER regardless of source."""
    EXPLICIT_EMPLOYER = "EXPLICIT_EMPLOYER"
    EXTRACTION_UNRESOLVED = "EXTRACTION_UNRESOLVED"
    EMPLOYER_UNDISCLOSED = "EMPLOYER_UNDISCLOSED"
    INTERMEDIARY_CLIENT_UNDISCLOSED = "INTERMEDIARY_CLIENT_UNDISCLOSED"


class HMState:
    DIRECT_HIRING_PERSON = "DIRECT_HIRING_PERSON"
    HIRING_TEAM_MEMBER = "HIRING_TEAM_MEMBER"
    RECRUITER = "RECRUITER"
    AGENCY_RECRUITER = "AGENCY_RECRUITER"
    POSTER_ONLY = "POSTER_ONLY"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"


class EmploymentState:
    FULL_TIME = "FULL_TIME"
    PART_TIME = "PART_TIME"
    INTERNSHIP = "INTERNSHIP"
    CONTRACT = "CONTRACT"
    TEMPORARY = "TEMPORARY"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_string(cls, value: str) -> str:
        return {
            "Full-time": cls.FULL_TIME,
            "Part-time": cls.PART_TIME,
            "Internship": cls.INTERNSHIP,
            "Contract": cls.CONTRACT,
            "Temporary": cls.TEMPORARY,
            "Freelance": cls.CONTRACT,
        }.get((value or "").strip(), cls.UNKNOWN)


# ── Signal patterns ( SCO P E: labelling only, never gating) ─────────────────

# Explicit "we will not name the employer" language.
UNDISCLOSED_PATTERNS = (
    r"not\s+to\s+disclose",
    r"company\s+not\s+disclosed",
    r"name\s+(withheld|not\s+disclosed)",
    r"\bconfidential\b[^.\n]{0,30}\b(?:company|employer|client)\b",
    r"\bstealth\b[^.\n]{0,30}\b(?:startup|company|mode)\b",
    r"undisclosed\s+(?:company|employer|client)",
)

# Third party posting someone else's vacancy.
INTERMEDIARY_PATTERNS = (
    r"agreement\s+with\s+(?:our\s+)?client",
    r"for\s+our\s+client",
    r"on\s+behalf\s+of\s+(?:our\s+)?client",
    r"vendor\s+partner",
    r"staffing\s+(?:agency|firm|partner)",
    r"recruitment\s+agency",
    r"\bi\s+am\s+not\s+hiring\b",
    r"share\s+job\s+to\s+help\s+job\s+seekers",
    r"for\s+regular\s+.*job\s+updates",
)

# Agency-flavoured poster names (only ever used TOGETHER with recruiter-tier
# evidence — never alone, so "Talent" inside a genuine employer name is safe).
AGENCY_POSTER_PATTERNS = (
    r"staffing",
    r"recruit(ment|ing|er)",
    r"hiring\s*hub",
    r"job\s*hub",
    r"\btalent\b",
    r"refer\s*me",
    r"pagaar",
)


def _any(patterns, text: str) -> Optional[str]:
    for pat in patterns:
        m = re.search(pat, text or "", re.IGNORECASE)
        if m:
            return m.group(0).strip()[:120]
    return None


def undisclosed_evidence(text: str) -> Optional[str]:
    """Verbatim snippet showing the employer is intentionally hidden."""
    return _any(UNDISCLOSED_PATTERNS, text)


def intermediary_evidence(text: str, author_name: str = "") -> Optional[str]:
    """Verbatim snippet showing a third party posted the vacancy."""
    hit = _any(INTERMEDIARY_PATTERNS, text)
    if hit:
        return hit
    if author_name and _any(AGENCY_POSTER_PATTERNS, author_name):
        return "agency-shaped poster: %s" % author_name.strip()[:80]
    return None


# ── Field-confidence derivation (18I, INTERNAL ONLY) ─────────────────────────
# Conservative: derived from the resolution state, never invented precision.
# The pipeline's overall confidence score is untouched.

STATE_CONFIDENCE = {
    ResolutionState.EXPLICIT: 0.95,
    ResolutionState.EXTRACTED: 0.80,
    ResolutionState.INFERRED_WITH_STRONG_EVIDENCE: 0.70,
    ResolutionState.INTERMEDIARY_UNDISCLOSED: 0.0,
    ResolutionState.NOT_FOUND: 0.0,
    ResolutionState.AMBIGUOUS: 0.0,
}


def field_confidence(resolution_state: str) -> float:
    return STATE_CONFIDENCE.get(resolution_state, 0.0)


@dataclass
class FieldResolution:
    """One field's audit label: value + how it was obtained + verbatim proof."""
    field: str
    value: str
    resolution: str
    evidence: str = ""
    confidence: float = 0.0


# ── Resolvers ────────────────────────────────────────────────────────────────

def resolve_company(text: str, job_card_company: str = "",
                    author_name: str = "") -> FieldResolution:
    """Company per the 18C evidence precedence (labelling only).

    1. Explicit employer statement in post ("X is hiring", "Join X", ...).
    2. Explicit job-card company. 3. Known-employer fallback. None of these
    infer from reposts, hashtags, clients, or author employers — and when the
    value stays Unclear the reason is one of the three EmployerStates.
    """
    from app.extraction import (clean_company_candidate,
                                extract_is_hiring_company,
                                extract_join_company)
    from app.config import KNOWN_INDIAN_COMPANIES

    join = extract_join_company(text or "")
    if join:
        return FieldResolution("company", join, ResolutionState.EXPLICIT,
                               "Join %s" % join, 0.95)
    hiring = extract_is_hiring_company(text or "")
    if hiring:
        m = re.search(r"%s\s+(?:is|are)\s+(?:hiring|looking\s+for)"
                      % re.escape(hiring), text or "", re.IGNORECASE)
        return FieldResolution(
            "company", hiring, ResolutionState.EXPLICIT,
            (m.group(0).strip()[:120] if m else "%s is hiring" % hiring), 0.95)
    at_m = re.search(
        r"\bAt\s+([A-Z][A-Za-z0-9&\.\-]*(?:[ \t]+[A-Z][A-Za-z0-9&\.\-]*){0,3})"
        r",?\s+we\s+are\s+hiring\b", text or "")
    if at_m:
        cand = clean_company_candidate(at_m.group(1))
        if cand:
            return FieldResolution("company", cand, ResolutionState.EXPLICIT,
                                   at_m.group(0).strip()[:120], 0.95)
    meta = clean_company_candidate(job_card_company or "")
    if meta:
        return FieldResolution("company", meta, ResolutionState.EXPLICIT,
                               "job card company: %s" % meta, 0.90)
    known = next(
        (c for c in KNOWN_INDIAN_COMPANIES
         if re.search(r"\b%s\b" % re.escape(c), (text or "").lower())), "")
    if known:
        return FieldResolution(
            "company", "Unclear", ResolutionState.INFERRED_WITH_STRONG_EVIDENCE,
            "known employer named (not attributed): %s" % known, 0.0)
    und = undisclosed_evidence(text or "")
    inter = intermediary_evidence(text or "", author_name or "")
    if und or inter:
        if inter and ("client" in (und or "").lower()
                      or "client" in (inter or "").lower()
                      or "not to disclose" in (und or "").lower()):
            state = EmployerState.INTERMEDIARY_CLIENT_UNDISCLOSED
        elif inter:
            state = EmployerState.INTERMEDIARY_CLIENT_UNDISCLOSED
        else:
            state = EmployerState.EMPLOYER_UNDISCLOSED
        return FieldResolution("company", "Unclear", ResolutionState.NOT_FOUND,
                               "%s=%s" % (state, (und or inter or "")[:120]), 0.0,
                               )
    # Multi-employer text that names no single hirer is ambiguous, not empty.
    from app.classifier import _distinct_employers  # local: classifier imports us nowhere
    try:
        multi = _distinct_employers(text or "") >= 2
    except Exception:
        multi = False
    if multi:
        return FieldResolution("company", "Unclear", ResolutionState.AMBIGUOUS,
                               "%s=multi-employer text" % EmployerState.EXTRACTION_UNRESOLVED,
                               0.0)
    return FieldResolution("company", "Unclear", ResolutionState.NOT_FOUND,
                           "%s=no employer evidence" % EmployerState.EXTRACTION_UNRESOLVED,
                           0.0)


def employer_state_of(resolution: FieldResolution) -> str:
    """The 18C tri-state for an Unclear company (EXPLICIT_EMPLOYER otherwise)."""
    if resolution.value != "Unclear":
        return EmployerState.EXPLICIT_EMPLOYER
    ev = resolution.evidence or ""
    for state in (EmployerState.INTERMEDIARY_CLIENT_UNDISCLOSED,
                  EmployerState.EMPLOYER_UNDISCLOSED,
                  EmployerState.EXTRACTION_UNRESOLVED):
        if ev.startswith(state + "="):
            return state
    return EmployerState.EXTRACTION_UNRESOLVED


def resolve_role(value: str, text: str = "") -> FieldResolution:
    """Role label: EXPLICIT when vacancy-framed verbatim, else EXTRACTED."""
    if not value or value in ("Unclear", "N/A"):
        return FieldResolution("role", value or "Unclear",
                               ResolutionState.NOT_FOUND,
                               "EXTRACTION_UNRESOLVED=no role extracted", 0.0)
    if re.search(r"https?://|lnkd\.in|📍|📌", value):
        return FieldResolution("role", value, ResolutionState.AMBIGUOUS,
                               "EXTRACTION_UNRESOLVED=link/emoji fused into title", 0.0)
    label = re.search(
        r"(?:role|position|job\s+title|title|opening|vacancy|designation)\s*"
        r"[:\|\-–—]\s*.{0,40}?%s" % re.escape(value[:30]), text or "",
        re.IGNORECASE)
    if label:
        return FieldResolution("role", value, ResolutionState.EXPLICIT,
                               label.group(0).strip()[:120], 0.95)
    return FieldResolution("role", value, ResolutionState.EXTRACTED,
                           "verbatim title in post", 0.80)


def resolve_location(value: str, india_evidence: str = "",
                     job_card_location: str = "") -> FieldResolution:
    if not value or value in ("Not Specified", "N/A"):
        return FieldResolution("location", value or "Not Specified",
                               ResolutionState.NOT_FOUND,
                               "EXTRACTION_UNRESOLVED=no location evidence", 0.0)
    if job_card_location and value.lower() in job_card_location.lower():
        return FieldResolution("location", value, ResolutionState.EXPLICIT,
                               "job card: %s" % job_card_location.strip()[:120],
                               0.95)
    if india_evidence:
        return FieldResolution("location", value, ResolutionState.EXPLICIT,
                               india_evidence.strip()[:120], 0.90)
    return FieldResolution("location", value, ResolutionState.EXTRACTED,
                           "post text", 0.80)


def resolve_employment(value: str) -> FieldResolution:
    state = EmploymentState.from_string(value)
    if state == EmploymentState.UNKNOWN:
        return FieldResolution("employment_type", value or "Unclear",
                               ResolutionState.NOT_FOUND,
                               "EXTRACTION_UNRESOLVED=employment unstated (UNKNOWN, not a rejection)",
                               0.0)
    return FieldResolution("employment_type", value, ResolutionState.EXPLICIT,
                           "explicit employment token: %s" % value, 0.95)


def resolve_experience(value: str, text: str = "") -> FieldResolution:
    if not value or value in ("Not Specified", "N/A"):
        return FieldResolution("experience", value or "Not Specified",
                               ResolutionState.NOT_FOUND,
                               "EXTRACTION_UNRESOLVED=no experience stated", 0.0)
    m = re.search(r"\d+\s*(?:\+|-|–)?\s*\d*\s*(?:years?|yrs?)|fresher",
                  text or "", re.IGNORECASE)
    return FieldResolution("experience", value, ResolutionState.EXTRACTED,
                           (m.group(0).strip()[:60] if m else "post text"), 0.80)


def resolve_hm(name: str, evidence: str, author_name: str = "",
               author_profile_url: str = "",
               text: str = "") -> FieldResolution:
    """HM relationship label (18H). Only evidence-backed attributions resolve;
    anything else is NOT_FOUND or AMBIGUOUS — never inferred."""
    ev = evidence or ""
    profile = author_profile_url or ""
    is_person = "/in/" in profile.lower() and "/company/" not in profile.lower()
    if not name or name in ("Unclear", "N/A"):
        if _any((r"\bi['\u2019]?m\s+hiring\b", r"\bwe['\u2019]?re\s+hiring\b"),
                text or "") and not is_person:
            return FieldResolution("hiring_manager", "Unclear",
                                   ResolutionState.NOT_FOUND,
                                   "POSTER_ONLY=hiring-side post, author not a person",
                                   0.0)
        return FieldResolution("hiring_manager", "Unclear",
                               ResolutionState.NOT_FOUND,
                               "%s=no hiring-side evidence" % HMState.NOT_FOUND,
                               0.0)
    if ev.startswith("named in post:"):
        return FieldResolution("hiring_manager", name,
                               ResolutionState.EXPLICIT,
                               "%s=%s" % (HMState.DIRECT_HIRING_PERSON, ev[:120]),
                               0.95)
    if ev.startswith("recruiter signature"):
        state = (HMState.AGENCY_RECRUITER
                 if _any(AGENCY_POSTER_PATTERNS, author_name or "") or
                 intermediary_evidence(text or "", author_name or "")
                 else HMState.RECRUITER)
        return FieldResolution("hiring_manager", name, ResolutionState.EXPLICIT,
                               "%s=%s" % (state, ev[:120]), 0.90)
    if ev.startswith("author states hiring side"):
        if is_person and name.strip().lower() == (author_name or "").strip().lower():
            return FieldResolution("hiring_manager", name,
                                   ResolutionState.EXPLICIT,
                                   "%s=%s" % (HMState.DIRECT_HIRING_PERSON,
                                              ev[:120]), 0.90)
        return FieldResolution("hiring_manager", name, ResolutionState.AMBIGUOUS,
                               "%s=name/evidence mismatch" % HMState.AMBIGUOUS,
                               0.0)
    if ev:
        return FieldResolution("hiring_manager", name,
                               ResolutionState.INFERRED_WITH_STRONG_EVIDENCE,
                               "%s=%s" % (HMState.HIRING_TEAM_MEMBER, ev[:120]),
                               0.70)
    return FieldResolution("hiring_manager", name, ResolutionState.AMBIGUOUS,
                           "%s=name without evidence" % HMState.AMBIGUOUS, 0.0)


def hm_state_of(resolution: FieldResolution) -> str:
    ev = resolution.evidence or ""
    for state in (HMState.DIRECT_HIRING_PERSON, HMState.HIRING_TEAM_MEMBER,
                  HMState.RECRUITER, HMState.AGENCY_RECRUITER,
                  HMState.POSTER_ONLY, HMState.NOT_FOUND, HMState.AMBIGUOUS):
        if ev.startswith(state + "=") or ev == state:
            return state
    if resolution.resolution == ResolutionState.NOT_FOUND:
        return HMState.NOT_FOUND
    return HMState.AMBIGUOUS


def audit_post_fields(classified) -> dict:
    """Label every 18D field of a ClassifiedPost. Pure labelling — the input
    object is never mutated and no decision is changed."""
    text = getattr(classified, "text", "") or ""
    company = resolve_company(
        text,
        job_card_company=getattr(classified, "job_card_company", ""),
        author_name=getattr(classified, "author_name", ""))
    # When the pipeline already resolved a company the audit must agree with
    # the stored value: only Unclear values get re-labelled by the resolver.
    stored_company = getattr(classified, "company_name", "Unclear")
    if stored_company not in ("Unclear", "N/A"):
        if (company.value == stored_company
                and company.resolution == ResolutionState.EXPLICIT):
            # Resolver and pipeline agree on an explicit statement — keep the
            # stronger label and its verbatim evidence.
            pass
        else:
            company = FieldResolution("company", stored_company,
                                      ResolutionState.EXTRACTED,
                                      "pipeline-resolved company preserved", 0.80)
    role = resolve_role(getattr(classified, "exact_role", "Unclear"), text)
    location = resolve_location(
        getattr(classified, "location", "Not Specified"),
        getattr(classified, "india_evidence", ""),
        getattr(classified, "job_card_location", ""))
    employment = resolve_employment(getattr(classified, "employment_type", "Unclear"))
    experience = resolve_experience(
        getattr(classified, "experience_requirement", "Not Specified"), text)
    hm = resolve_hm(getattr(classified, "hiring_manager_name", "Unclear"),
                    getattr(classified, "hiring_manager_evidence", ""),
                    getattr(classified, "author_name", ""),
                    getattr(classified, "author_profile_url", ""), text)
    out = {}
    for fr in (company, role, location, employment, experience, hm):
        out[fr.field] = {"value": fr.value, "resolution": fr.resolution,
                         "evidence": fr.evidence,
                         "confidence": field_confidence(fr.resolution)
                         if fr.field != "company" or fr.value != "Unclear"
                         else 0.0}
    out["company"]["employer_state"] = employer_state_of(company)
    out["hiring_manager"]["hm_state"] = hm_state_of(hm)
    out["employment_type"]["employment_state"] = EmploymentState.from_string(
        getattr(classified, "employment_type", "Unclear"))
    return out
