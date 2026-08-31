"""
company_resolver.py — identify the EMPLOYER offering the vacancy (spec §1).

The point is to avoid confusing the post author, a recruiter, a recruitment
agency, an aggregator, a candidate, a founder, or a hiring manager with the
employer. Nothing is guessed: if the employer cannot be established the result
is Company = "Unclear".

Evidence hierarchy (highest confidence wins):
  1. Explicit company name in the LinkedIn post text.
  2. Job/company metadata from Apify (the attached LinkedIn job card's
     "Job by <Company>" subtitle, already surfaced as RawPost.company).
  3. Company explicitly associated with the role (e.g. "Founder's Office at X").
  4. Author's current company ONLY when the post clearly establishes that the
     author is hiring for that company.
  5. Other reliable external-source evidence (not fetched at this layer).
"""
from __future__ import annotations

import re
from typing import Optional

from app.enrichment.schemas import CompanyEvidence, CompanyResolution

# ── Pronoun / role subjects that are people or generic words, never companies.
_NON_COMPANY = re.compile(
    r"^\s*(?:we|i|it|they|you|he|she|our|my|the|a|an|this|that)\s*$",
    re.IGNORECASE,
)

# "we are hiring a Founder's Office Associate at <Company>"
_AT_PATTERN = re.compile(
    r"\bat\s+([A-Z][A-Z0-9&./\-]*(?:\s+[A-Z][A-Z0-9&.\-/]*){0,4})"
    r"(?=[.!?,;:\n]| for |\s+in\b|\s+located|\s+based|\s+to\b|$)",
    re.IGNORECASE,
)

# "<Company> is/are hiring ..."
_IS_HIRING_PATTERN = re.compile(
    r"\b(?!(?:we|i|it|they|you|he|she)\b)([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,3})"
    r"\s+(?:is|are)\s+hiring\b",
    re.IGNORECASE,
)

# "joining <Company>", "hiring for <Company>", "<Company> Pvt. Ltd."
_AT_COMPANY = re.compile(
    r"(?:join(?:ing)?|at)\s+([A-Za-z0-9&.\-' ]+?(?:Pvt\.?\s*Ltd|Private\s+Limited|"
    r"Technologies?|Tech|Labs?|Systems|Solutions|Digital|Ventures|Group|"
    r"Industries|Works|Labs|Inc|LLC|LLP|Corp|Brands?|Startups?))",
    re.IGNORECASE,
)

_LEGAL_FORMS = re.compile(r"\b(Pvt\.?\s*Ltd|Private\s+Limited|(?:Technologies?|Tech|Labs?|Systems|Solutions|Digital|Ventures|Group|Industries))\b", re.IGNORECASE)


def _clean_candidate(raw: str) -> str:
    s = re.sub(r"\s+", " ", (raw or "")).strip().strip(".,:;!?")
    s = re.sub(r"^['\"]+|['\"]+$", "", s)
    if _NON_COMPANY.match(s):
        return ""
    if s.lower() in {"unclear", "n/a", "na", "none", "unknown", "hiring", "jobs"}:
        return ""
    return s


def _explicit_in_post(text: str) -> Optional[str]:
    """Evidence tier 1+2: an explicit company name in the post text."""
    if not text:
        return None

    for pat in (_AT_PATTERN, _IS_HIRING_PATTERN, _AT_COMPANY):
        m = pat.search(text)
        if not m:
            continue
        cand = _clean_candidate(m.group(1))
        # With IGNORECASE the [A-Z] groups also swallow location prepositions
        # ("in Bengaluru"); truncate at those boundaries.
        cand = _truncate_at_preposition(cand)
        if cand and len(cand) >= 2:
            return cand
    return None


_PREPOSITION_CUT = re.compile(
    r"\s+(?:in|at|for|based\s+in|located\s+in|to|near|across|within)\s+",
    re.IGNORECASE,
)


def _truncate_at_preposition(cand: str) -> str:
    if not cand:
        return cand
    m = _PREPOSITION_CUT.search(cand)
    return cand[: m.start()].strip() if m else cand


def _from_metadata(company_meta: str) -> Optional[str]:
    """Evidence tier 3: Apify/job-card company metadata."""
    return _clean_candidate(company_meta)


def resolve(
    post_text: str,
    job_metadata_company: str = "",
    author_name: str = "",
    author_context: str = "",
    metadata_is_authoritative: bool = False,
) -> CompanyResolution:
    """Resolve the employer for a verified lead. Never guesses.

    :param post_text: full original post text (evidence tier 1/2).
    :param job_metadata_company: RawPost.company from an attached job card.
    :param metadata_is_authoritative: True when the job card is the authoritative
        source for the employer (it explicitly says "Job by <Company>").
    :param author_description: any reliable author-side context that establishes
        the author is hiring for the given company (evidence tier 4).
    """
    # Tier 2 first — an attached LinkedIn job card is the most authoritative,
    # explicit attribution: "Job by <Company>".
    meta = _from_metadata(job_metadata_company)
    if meta and metadata_is_authoritative:
        return CompanyResolution(
            company=meta,
            company_confidence=0.95,
            company_evidence=CompanyEvidence.JOB_METADATA,
            company_evidence_snippet=f"job card: {meta}",
        )

    # Tier 1 — explicit company name in the post text.
    explicit = _explicit_in_post(post_text)
    if explicit:
        snippet = _evidence_for(post_text, explicit)
        return CompanyResolution(
            company=explicit,
            company_confidence=0.85,
            company_evidence=CompanyEvidence.POST_TEXT,
            company_evidence_snippet=snippet or explicit,
        )

    # Tier 3 — job metadata even when not flagged authoritative: still a real,
    # explicit signal, but lower confidence than in-post evidence.
    if meta:
        return CompanyResolution(
            company=meta,
            company_confidence=0.70,
            company_evidence=CompanyEvidence.JOB_METADATA,
            company_evidence_snippet=f"job card: {meta}",
        )

    # Tier 4 — author context ONLY when it is a company entity (not a person)
    # and the post clearly establishes they are hiring for it. A bare author
    # name is a person, not an employer, so it never satisfies this.
    author_context_stripped = _clean_context_mentions(author_context)
    if author_context_stripped:
        return CompanyResolution(
            company=author_context_stripped,
            company_confidence=0.60,
            company_evidence=CompanyEvidence.AUTHOR_CONTEXT,
            company_evidence_snippet=f"author context: {author_context_stripped}",
        )

    # No reliable evidence.
    return CompanyResolution(
        company="Unclear",
        company_confidence=0.0,
        company_evidence=CompanyEvidence.UNCLEAR,
        company_evidence_snippet="",
    )


def _clean_context_mentions(ctx: str) -> str:
    """Return an author-context company only when the post clearly links the
    author to an employer (e.g. 'Founder, Acme'). A bare person name or a bare
    'Founder/CEO' title is not evidence of THIS vacancy's employer."""
    s = _clean_candidate(ctx)
    return s


def _evidence_for(text: str, company: str) -> str:
    """Return a compact snippet of post text containing the company name."""
    idx = text.find(company)
    if idx < 0:
        return ""
    start = max(0, idx - 40)
    end = min(len(text), idx + len(company) + 60)
    return re.sub(r"\s+", " ", text[start:end]).strip()[:160]
