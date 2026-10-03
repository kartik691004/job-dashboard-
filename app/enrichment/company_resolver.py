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
from app.extraction import (
    GENERIC_DESCRIPTOR_WORDS as _GENERIC_DESCRIPTOR_WORDS,
    extract_join_company,
)

# ── Pronoun / role subjects that are people or generic words, never companies.
_NON_COMPANY = re.compile(
    r"^\s*(?:we|i|it|they|you|he|she|our|my|the|a|an|this|that)\s*$",
    re.IGNORECASE,
)

# "we are hiring a Founder's Office Associate at <Company>"
# NOTE (Phase 16): intra-name separators are [ \t], never \n — a company name
# never spans a line break ("\s+" fused "MovieMe\n\nMovieMe" into one name).
_AT_PATTERN = re.compile(
    r"\bat\s+([A-Z][A-Z0-9&./\-]*(?:[ \t]+[A-Z][A-Z0-9&.\-/]*){0,4})"
    r"(?=[.!?,;:\n]| for |\s+in\b|\s+located|\s+based|\s+to\b|$)",
    re.IGNORECASE,
)

# "<Company> is/are hiring / building / looking for ..."
# (a) Two negative lookbehinds prevent matching inside possessive / curly-
#     apostrophe phrases: (?<!['\u2019]) blocks "Founder\u2019s → s ...",
#     (?<!['\u2019]s\s) blocks "Founder\u2019s Office → Office ...".
# (b) Dot removed from word character class prevents matching across sentence
#     boundaries ("...space in India.\n\nWe are looking for..." → "space").
# (c) "who" blocked to prevent relative-clause matches ("Who we are looking
#     for" is NOT a hiring statement).
_IS_HIRING_PATTERN = re.compile(
    r"\b(?!(?:we|who|i|it|they|you|he|she)\b)(?<!['\u2019])(?<!['\u2019]s\s)"
    r"([A-Z][A-Za-z0-9&\-]*(?:[ \t]+[A-Z][A-Za-z0-9&\-]*){0,3})"
    r"\s+(?:is|are)\s+(?:hiring\b|building\b|looking\s+for\b)",
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

# "| <Company> LLP / Ltd / Technologies ... |" — a card/header line where the
# company name sits on the other side of a "|" separator (e.g. the styled
# "We're Hiring: Founder's Office | Noida | Amama Partners LLP" header).
_HEADER_COMPANY = re.compile(
    r"(?<=\|)\s*([A-Z][A-Za-z0-9&.\-' ]*?(?:Pvt\.?\s*Ltd|Private\s+Limited|"
    r"Technologies?|Tech|Labs?|Systems|Solutions|Ventures|Group|Industries|"
    r"Works|Inc|LLC|LLP|Corp|Brands?|Startups?)\b)",
    re.IGNORECASE,
)

# Phase 30.1 — explicit company-header lines: "🏢 Company: Flipkart",
# "Company: Flipkart", "COMPANY: Flipkart". Narrow by construction:
#   (a) the line must START with only non-word characters (emoji/symbols/
#       whitespace) before the literal label "company" — so "my company is…"
#       or "our company: …" prose never matches (a word char before the label
#       kills the match);
#   (b) the label must be followed by an explicit field separator (':' '-');
#   (c) the value is bounded to the SAME line — a company name never spans a
#       line break (Phase 16 rule) — and is truncated at in-line field
#       boundaries ('|', '#', dashes, URLs) before the shared
#       _clean_candidate/_truncate_at_preposition/_is_generic_descriptor
#       guards run (junk, lowercase prose, generic descriptors rejected).
# No author/employer inference: only an explicitly labeled field is read.
_COMPANY_HEADER = re.compile(
    r"^[^\w\n]*company\s*[:\-\u2013]\s*([^\n]+)$",
    re.IGNORECASE | re.MULTILINE,
)

# In-line value boundaries for company-header values (Phase 30.1).
_HEADER_VALUE_BOUNDARY = re.compile(
    r"\s*\|\s*|\s*[\u2013\u2014]\s*|\s+-\s+|\s*#|\s+https?://\S*",
)

# "leading <Company>, an organisation/startup" and "help me lead <Company>, ..."
_ORG_LEAD_RE = re.compile(
    r"\b(?:help(?:ing|s|ed)?\s+(?:me|us|them)?\s*)?lead(?:ing|er)?\s+"
    r"([A-Z][A-Za-z0-9&.\-]*)\s*,?\s+(?:an?|the)\s+"
    r"(?:organisation|organization|company|startup|firm|team)\b",
    re.IGNORECASE,
)

# Generic-descriptor guard uses the shared set from app.extraction (single
# source of truth with the classifier — both layers reject the same
# non-employers).
def _is_generic_descriptor(cand: str) -> bool:
    """True when a captured 'company' is actually a generic quantified/
    descriptive phrase (e.g. 'a fast-growing MedTech', 'a leading startup',
    'a fintech company') rather than a named employer. A real brand that
    contains any non-generic proper-noun token is preserved."""
    words = [w for w in re.split(r"[^a-z0-9]+", (cand or "").lower()) if w]
    if not words:
        return True
    return all(w in _GENERIC_DESCRIPTOR_WORDS for w in words)


def _clean_candidate(raw: str) -> str:
    s = re.sub(r"\s+", " ", (raw or "")).strip().strip(".,:;!?")
    s = re.sub(r"^['\"]+|['\"]+$", "", s)
    if _NON_COMPANY.match(s):
        return ""
    if s.lower() in {"unclear", "n/a", "na", "none", "unknown", "hiring", "jobs"}:
        return ""
    # Phase 16: an all-lowercase capture is prose or an email fragment, never
    # an employer brand (baseline: "mail me at sonia.malhotra@…" captured
    # company "sonia"). A real brand carries at least one capitalized token.
    # (Lowercase-first brands like "devx" degrade to Unclear — fail-closed.)
    if not any(w and (w[0].isupper() or w[0].isdigit()) for w in s.split()):
        return ""
    return s


def _explicit_in_post(text: str) -> Optional[str]:
    """Evidence tier 1+2: an explicit company name in the post text."""
    if not text:
        return None

    # Phase 16: "Join <Company>" (employer self-identification) wins over
    # "at <Name>" (which also matches clients: baseline Enqurious resolved to
    # "Fractal", a client named in "teams at Fractal, Tredence, …", while the
    # post opens "Join Enqurious, …").
    joined = extract_join_company(text)
    if joined:
        return joined

    # Phase 30.1: an explicitly labeled company header ("🏢 Company: Flipkart")
    # outranks the prose patterns below — the label names the field itself.
    headered = _company_header(text)
    if headered:
        return headered

    for pat in (_AT_PATTERN, _IS_HIRING_PATTERN, _AT_COMPANY, _HEADER_COMPANY, _ORG_LEAD_RE):
        m = pat.search(text)
        if not m:
            continue
        cand = _clean_candidate(m.group(1))
        # With IGNORECASE the [A-Z] groups also swallow location prepositions
        # ("in Bengaluru"); truncate at those boundaries.
        cand = _truncate_at_preposition(cand)
        # Reject generic quantified / descriptive phrases ("a fast-growing
        # MedTech startup", "a leading company") that are captured as if they
        # were an employer name. Only an actual named employer is surfaced.
        if cand and len(cand) >= 2 and not _is_generic_descriptor(cand):
            return cand
    return None


def _company_header(text: str) -> Optional[str]:
    """Phase 30.1: company from an explicitly labeled header line, else None.

    Reads ONLY lines whose label literally is "Company" (optionally preceded by
    emoji/symbols) followed by ':' or '-'. Values are bounded to the line,
    truncated at in-line field boundaries, and must pass the same shared
    guards as every other candidate (clean, capitalized, non-generic).
    Fail-closed: anything ambiguous yields None, never a guess.
    """
    if not text:
        return None
    for m in _COMPANY_HEADER.finditer(text):
        cand = _HEADER_VALUE_BOUNDARY.split(m.group(1), maxsplit=1)[0]
        cand = cand.strip().strip("*_~`").strip()
        cand = _clean_candidate(cand)
        cand = _truncate_at_preposition(cand)
        if cand and len(cand) >= 2 and not _is_generic_descriptor(cand):
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
