"""
enricher.py — the Phase 6 lead-enrichment orchestrator.

Runs AFTER a lead has passed every existing verification gate (ACCEPT). It
only makes an already-valid lead more complete and actionable. It NEVER:
  - turns a REJECT into an ACCEPT (the verifier still owns vacancy semantics)
  - fabricates information
  - changes the verification decision / confidence

Enrichment is deterministic-first (regex + evidence, no invention) and may
optionally use an LLM for semantic extraction of an already-ACCEPTED lead. An
LLM provider is NOT required: without one the enricher degrades to pure
deterministic extraction and every field still honours its safe default.

Output: an `EnrichedLead` carrying both the original verified fields and the
new enrichment fields, plus a separate `enrichment_confidence` and
`enrichment_status` (READY / READY_WITHOUT_CONTACT / REVIEW).
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from app.enrichment import schemas as S
from app.enrichment.company_resolver import resolve as resolve_company
from app.enrichment.contact_resolver import resolve as resolve_contact
from app.enrichment.email_resolver import resolve as resolve_email
from app.contact.schemas import ContactEvidence, ContactStatus

# ── Location normalisation ─────────────────────────────────────────────────────
# spec §3: normalise obvious variants, preserve India context.
_LOCATION_CANON = {
    "bangalore": "Bengaluru, Karnataka, India",
    "bengaluru": "Bengaluru, Karnataka, India",
    "gurgaon": "Gurugram, Haryana, India",
    "gurugram": "Gurugram, Haryana, India",
    "noida": "Noida, Uttar Pradesh, India",
    "delhi": "New Delhi, India",
    "new delhi": "New Delhi, India",
    "mumbai": "Mumbai, Maharashtra, India",
    "bombay": "Mumbai, Maharashtra, India",
    "hyderabad": "Hyderabad, Telangana, India",
    "pune": "Pune, Maharashtra, India",
    "chennai": "Chennai, Tamil Nadu, India",
    "kolkata": "Kolkata, West Bengal, India",
    "ahmedabad": "Ahmedabad, Gujarat, India",
    "jaipur": "Jaipur, Rajasthan, India",
    "tirupur": "Tirupur, Tamil Nadu, India",
    "tiruppur": "Tirupur, Tamil Nadu, India",
    "tirpur": "Tirupur, Tamil Nadu, India",
}

_REMOTE_RE = re.compile(r"\b(remote|wfh|work\s+from\s+home)\b", re.IGNORECASE)

# ── Employment type (spec §4) ─────────────────────────────────────────────────
_EMP_FULL = re.compile(r"\b(full-time|full\s+time|permanent|fulltime)\b", re.IGNORECASE)
_EMP_INTERN = re.compile(r"\bintern(ship|ing)?\b", re.IGNORECASE)
_EMP_PART = re.compile(r"\b(part-time|part\s+time|parttime)\b", re.IGNORECASE)
_EMP_CONTRACT = re.compile(r"\bcontract(ual)?\b", re.IGNORECASE)
_EMP_FREELANCE = re.compile(r"\bfreelance(d|r)?\b", re.IGNORECASE)
_EMP_TEMP = re.compile(r"\btemporary|temp\b", re.IGNORECASE)

# ── Experience for THIS role (spec §5) ────────────────────────────────────────
# Captures "0-2 years", "1–3 years", "3+ years", "5+ Yrs", "freshers".
_EXP = re.compile(
    r"(?:(\d{1,2})\s*[\-\u2013\u2014–]\s*(\d{1,2})\s*(?:ye?a?rs?|yrs?|yr|years of experience))"
    r"|(?:(\d{1,2})\s*\+\s*(?:ye?a?rs?|yrs?|yr)|"
    r"(?:ye?a?rs?|yrs?|yr)\s*(\d{1,2})\s*\+)|"
    r"(\d{1,2})\s*(?:to|-\s*)\s*(\d{1,2})\s*(?:ye?a?rs?|yrs?)",
    re.IGNORECASE,
)
_EXP_FRESHER = re.compile(r"\bfreshers?\b|\bfresher\s+welcome", re.IGNORECASE)
# "founder has 10 years experience" — must NOT be read as the vacancy requirement.
_EXPERIENCE_OWNER = re.compile(
    r"\b(founder|ceo|co-founder|i have|have\s+\d|with\s+\d|possess(?:es)?\s+\d|"
    r"background of|brings?\s+\d)\b",
    re.IGNORECASE,
)

# ── CTC / compensation (spec §6) ──────────────────────────────────────────────
_CTC = re.compile(
    r"(\u20b9?|INR|Rs\.?)?\s*(\d{1,2}(?:\.\d+)?)[\s\-\u2013]?"
    r"(?:to[\s\-]?|\-\u2013?)?\s*(\d{1,2}(?:\.\d+)?)?\s*"
    r"(LPA|Lacs?|Lakhs?|lpa|cr|Crore|k|K|thousand)\b",
    re.IGNORECASE,
)
_CTC_USD = re.compile(r"\$(\d+(?:k|K)?)\s*[-–]?\s*(\d+(?:k|K))?\s*(?:per\s+annum)?\b", re.IGNORECASE)

# ── Default sentinels ─────────────────────────────────────────────────────────
_NOT_COMPANY_KEYWORDS = {"Chief of Staff / Founder's Office / Generalist",
                         "Founder's Office / Chief of Staff / Generalist"}


def _has_any(text: str, patterns) -> bool:
    return any(p.search(text) for p in patterns)


def normalize_location(classified_location: str, post_text: str, job_card_location: str = "") -> S.LocationResolution:
    """Return a normalised location. Never converts bare 'Remote' to
    'Remote - India' unless India eligibility is explicitly established."""
    text = (post_text or "") + " " + (classified_location or "") + " " + (job_card_location or "")
    low = text.lower()

    canonical = "Not Specified"
    evidence = ""
    # Only accept a city when it is joined by India context somewhere.
    for key, canon in _LOCATION_CANON.items():
        if key in low:
            canonical = canon
            evidence = key
            break

    is_remote = bool(_REMOTE_RE.search(low))
    remote_is_india = False
    if is_remote:
        # Remote - India ONLY when explicit India eligibility is established.
        india_markers = ("india", "noida", "bangalore", "bengaluru", "mumbai", "pune",
                         "hyderabad", "chennai", "delhi", "gurgaon", "gurugram", "lpa",
                         "inr", "rupees", "lakh", "kolkata")
        # Do not count 'Remote' itself as India.
        if any(m in low for m in india_markers):
            remote_is_india = True
            canonical = "Remote - India"
            evidence = "remote + India eligibility"
        else:
            canonical = "Remote"
            evidence = "remote (India not explicitly established)"

    conf = 0.9 if canonical not in ("Not Specified",) else 0.0
    return S.LocationResolution(
        location=canonical,
        location_confidence=conf,
        location_evidence_snippet=evidence,
        is_remote=is_remote,
        remote_is_india=remote_is_india,
    )


def resolve_exact_role(existing_exact_role: str, llm_actual_role: str,
                       llm_exact_role: str, post_text: str) -> S.ExactRoleResolution:
    """Return the ACTUAL advertised role title, never a keyword list.

    Priority: the verifier's `actual_role` / `exact_role` (already curated by the
    LLM as the real vacancy title), then a curated deterministic capture, then
    Unclear. The generic keyword-list string is always rejected.

    ANTI-DATA-QUALITY GUARD (Phase 7): candidates that are sentence fragments
    (a keyword substring appended with hiring verbs — e.g. "We are hiring a
    Founder's Office Associate at Acme i") are NOT titles and are rejected.
    Only a clean noun-phrase title (or a curated actual_role) is used.
    """
    _TITLE_START_REJECT = re.compile(
        r"^(?:we\s+(?:are|'|i?re)\s+|we['\u2019]?re\s+|"
        r"i['\u2019]?m\s+|we\s+are\s+looking\s+for\s+|"
        r"looking\s+for\b|hiring\s+(?:an?\s+|a\s+)?|now\s+hiring\b|"
        r"open(?:ing)?\s+for\b|join\s+(?:our|us)\b|apply\b|"
        r"apply\s+now\b|welcome\b|we\s+hired\b)",
        re.IGNORECASE,
    )
    candidates = []
    for cand in (llm_actual_role, llm_exact_role, existing_exact_role):
        if cand and cand not in _NOT_COMPANY_KEYWORDS and cand.lower() != "unclear":
            c = cand.strip()
            # Reject obvious sentence fragments, but DO protect curated LLM
            # actual_role (the primary source of truth) — it is authoritative.
            if cand is existing_exact_role and \
                    _TITLE_START_REJECT.match(c) and \
                    not re.match(r"^(?:chief\s+of\s+staff|founder)", c, re.IGNORECASE):
                continue
            candidates.append(c)
    if candidates:
        best = candidates[0]
        if len(best) > 120:
            best = best[:120].rstrip()
        return S.ExactRoleResolution(
            exact_role=best,
            major_category="Unclear",
            role_confidence=0.85,
            role_evidence_snippet=best[:80],
        )

    # Deterministic capture of the ACTUAL advertised title around the role
    # keyword. Captures the role phrase plus a bounded, sentence-safe trailing
    # descriptor (e.g. "Chief of Staff - India Country Manager's Office",
    # "Founder's Office Associate / Cadet / Executive") — NOT a whole sentence.
    role_kw = re.compile(
        r"(chief\s+of\s+staff|founder['\u2019]?s?\s+office|"
        r"founder['\u2019]?s?\s+associate|founder\s+associate|"
        r"office\s+of\s+the\s+founder)\b",
        re.IGNORECASE,
    )
    m = role_kw.search(post_text or "")
    if m:
        # Start from the keyword; keep only descriptor words up to a sentence
        # boundary, a location preposition, or a soft cap.
        tail = post_text[m.end():].strip()
        tail = re.split(r"(?<=[.;:!?])\s+|\.\s*$", tail, maxsplit=1)[0]
        # Drop the role's trailing location / qualifier preposition regardless of
        # whether it sits at the very start (post .strip()) or mid-phrase.
        tail = re.split(
            r"(?:^|\s)(?:in|at|based\s+in|located\s+in|for|across|within)(?=\s|$)",
            tail, maxsplit=1)[0]
        tail_words = tail.split()
        kept = []
        for w in tail_words:
            if w in (",", ")", "(", "|", ":", ";") or w.startswith("#"):
                break
            if re.fullmatch(r"\d+(?:\.\d+)?(?:-?\d+)?", w):
                break
            if w.lower() in ("lpa", "inr", "ctc", "apply", "applynow", "please"):
                break
            kept.append(w)
            if len(kept) >= 6:   # soft cap on descriptor length
                break
        # A leading "-" is a title separator (e.g. "Chief of Staff - India");
        # keep it, only strip a bare lone dash.
        if kept and kept[0] == "-" and len(kept) == 1:
            kept = []
        trailing = " ".join(kept).strip().strip(",:;")
        # Only accept trailing when it reads as part of a title, not a verb/sentence.
        if trailing and not re.match(r"^(?:is|are|we|apply|please|\d|hiring|join|and)", trailing, re.IGNORECASE):
            title = f"{m.group(1).strip()} {trailing}".strip()
        else:
            title = m.group(1).strip()
        title = title[:120]
        if len(title) > 3:
            return S.ExactRoleResolution(
                exact_role=title,
                major_category="Unclear",
                role_confidence=0.6,
                role_evidence_snippet=title[:80],
            )
    return S.ExactRoleResolution(exact_role="Unclear", role_confidence=0.0)


def resolve_employment(post_text: str, classified_employment: str, llm_employment: str) -> S.EmploymentResolution:
    text = post_text or ""
    emp = "Unclear"
    conf = 0.0
    evidence = ""

    if _EMP_INTERN.search(text):
        emp, conf, evidence = "Internship", 1.0, "internship keyword"
    elif _EMP_PART.search(text):
        emp, conf, evidence = "Part-time", 0.95, "part-time keyword"
    elif _EMP_CONTRACT.search(text):
        emp, conf, evidence = "Contract", 0.9, "contract keyword"
    elif _EMP_FREELANCE.search(text):
        emp, conf, evidence = "Freelance", 0.9, "freelance keyword"
    elif _EMP_TEMP.search(text):
        emp, conf, evidence = "Temporary", 0.85, "temporary keyword"
    elif _EMP_FULL.search(text):
        emp, conf, evidence = "Full-time", 0.95, "full-time keyword"
    else:
        # Fall back to the verifier/classifier judgement only when explicit.
        for cand in (llm_employment, classified_employment):
            if cand in ("Full-time", "Contract", "Part-time", "Internship", "Freelance", "Temporary"):
                emp, conf = cand, 0.6
                evidence = f"verifier judgment: {cand}"
                break

    return S.EmploymentResolution(
        employment_type=emp,
        employment_confidence=conf,
        employment_evidence_snippet=evidence,
    )


def resolve_experience(post_text: str, classified_exp: str, llm_exp: str) -> S.ExperienceResolution:
    text = post_text or ""
    # Do not read the founder's own experience as the candidate's requirement.
    low = text.lower()
    if _EXPERIENCE_OWNER.search(low):
        owner_span = _EXPERIENCE_OWNER.search(low)
        if "founder" in owner_span.group(0).lower() or "ceo" in owner_span.group(0).lower():
            # A founder-experience clause is not a vacancy requirement; ignore it.
            pass

    m = _EXP.search(low)
    if m:
        bound = m.group(0).strip()
        return S.ExperienceResolution(
            experience_required=bound.replace("\u2013", "-"),
            experience_confidence=0.9,
            experience_evidence_snippet=bound[:40],
        )
    if _EXP_FRESHER.search(low):
        return S.ExperienceResolution(
            experience_required="Freshers",
            experience_confidence=0.9,
            experience_evidence_snippet="freshers",
        )
    for cand in (classified_exp, llm_exp):
        if cand and cand != "Not Specified" and cand.lower() != "unclear":
            return S.ExperienceResolution(
                experience_required=cand,
                experience_confidence=0.6,
                experience_evidence_snippet="verifier judgment",
            )
    return S.ExperienceResolution(experience_required="Not Specified", experience_confidence=0.0)


def resolve_ctc(post_text: str, classified_ctc: str, llm_ctc: str, llm_currency: str) -> S.CTCResolution:
    text = post_text or ""
    m = _CTC.search(text) or _CTC_USD.search(text)
    if m:
        raw = re.sub(r"\s+", " ", m.group(0)).strip()
        return S.CTCResolution(
            ctc=raw,
            ctc_confidence=0.9,
            ctc_evidence_snippet=raw[:40],
        )
    for cand in (classified_ctc, llm_ctc):
        if cand and cand != "Not Disclosed":
            if llm_currency and llm_currency.lower() not in cand.lower():
                cand = f"{cand} {llm_currency}".strip()
            return S.CTCResolution(
                ctc=cand,
                ctc_confidence=0.6,
                ctc_evidence_snippet="verifier judgment",
            )
    return S.CTCResolution(ctc="Not Disclosed", ctc_confidence=0.0)


def _enrichment_confidence(e: S.EnrichedLead) -> float:
    """A separate completeness score (spec §13), independent from verification
    confidence. 0.90+ highly complete; 0.75+ usable; 0.50+ incomplete; <0.5 poor.
    Missing email / hiring manager are NOT heavily penalised (they are commonly
    absent and must not reject a genuine vacancy). Unclear company or role is
    heavily penalised because those make a lead unusable."""
    score = 0.0
    weights = {
        "company": 0.20,
        "role": 0.20,
        "location": 0.12,
        "employment": 0.10,
        "experience": 0.08,
        "ctc": 0.08,
        "source": 0.10,
        "contact": 0.04,   # hiring manager (small — not required)
        "email": 0.02,     # email (tiny — not required)
    }
    if e.company_name not in ("Unclear", "", "N/A"):
        score += weights["company"]
    if e.exact_role not in ("Unclear", ""):
        score += weights["role"]
    if e.location not in ("Not Specified", ""):
        score += weights["location"]
    if e.employment_type not in ("Unclear", ""):
        score += weights["employment"]
    if e.experience_required not in ("Not Specified", ""):
        score += weights["experience"]
    if e.ctc not in ("Not Disclosed", ""):
        score += weights["ctc"]
    if e.source_link:
        score += weights["source"]
    if e.hiring_manager_name not in ("Unclear", ""):
        score += weights["contact"]
    if e.cold_email and e.cold_email != "Not Available":
        score += weights["email"]
    return round(min(1.0, score), 2)


def _data_quality_score(e: S.EnrichedLead) -> float:
    """Phase 8 transparent quality score — factors and weights are explicit.

    INDEPENDENT OF CONTACT AVAILABILITY: a genuine job with no public email /
    hiring manager can still score well and be released as READY_WITHOUT_CONTACT
    (contact factors are capped at 0.12 of the 1.00 total).

    Weights (documented, sum = 1.00):
      0.15 verified company    0.15 exact role       0.15 India relevance
      0.10 current vacancy     0.10 full-time        0.08 location
      0.05 experience          0.05 CTC              0.05 evidence quality
      0.04 hiring manager      0.04 manager LinkedIn 0.04 verified email
    """
    score = 0.0
    score += 0.15 if e.company_name not in ("Unclear", "", "N/A") else 0.0
    score += 0.15 if e.exact_role not in ("Unclear", "") else 0.0

    india = (e.india_relevance or "").lower()
    if india in ("india", "remote - india", "indian company", "india + global"):
        score += 0.15
    elif india in ("remote", ""):
        score += 0.0 if not india else 0.05
    else:
        score += 0.05

    score += 0.10 if (e.verification_status or "").upper() in (
        "NEW", "REVIEW", "READY", "ACCEPT") else 0.0
    if e.employment_type == "Full-time":
        score += 0.10
    elif e.employment_type in ("Unclear", "Not Specified", ""):
        score += 0.05
    # Other employment types already failed the hard gates upstream.

    score += 0.08 if e.location not in ("Not Specified", "") else 0.0
    score += 0.05 if e.experience_required not in ("Not Specified", "") else 0.0
    score += 0.05 if e.ctc not in ("Not Disclosed", "") else 0.0

    evidence_fields = [
        e.company_evidence_snippet, e.role_evidence_snippet,
        getattr(e, "location_evidence_snippet", ""),
        getattr(e, "employment_evidence_snippet", ""),
        getattr(e, "experience_evidence_snippet", ""),
        getattr(e, "ctc_evidence_snippet", ""),
        e.hiring_manager_evidence_snippet, e.email_evidence_url,
    ]
    filled = sum(1 for s in evidence_fields if (s or "").strip())
    score += 0.05 * (filled / len(evidence_fields))

    score += 0.04 if e.hiring_manager_name not in ("Unclear", "") else 0.0
    if e.hiring_manager_linkedin not in ("Not Available", ""):
        score += 0.04
    if e.cold_email and e.cold_email != "Not Available":
        score += 0.04

    return round(min(1.0, score), 2)


def _enrichment_status(e: S.EnrichedLead) -> S.EnrichmentStatus:
    """Compute READY / READY_WITHOUT_CONTACT / REVIEW (spec §14).

    READY: company + exact role known, India valid, acceptable employment,
           source present.
    Missing email / hiring manager alone -> READY_WITHOUT_CONTACT (not review).
    Unclear company OR exact role -> REVIEW (never pretend it is complete).
    """
    genuine = e.verification_status in ("New", "Review", "READY")
    india_ok = e.remote_is_india or _india_in_location(e.location) or e.verification_status == "New"
    acceptable_emp = e.employment_type in ("Full-time", "Unclear", "Not Specified") or \
        e.employment_type in ("Contract", "Part-time", "Internship", "Freelance", "Temporary")

    company_ok = e.company_name not in ("Unclear", "", "N/A")
    role_ok = e.exact_role not in ("Unclear", "")
    source_ok = bool(e.source_link)

    if not company_ok or not role_ok:
        return S.EnrichmentStatus.REVIEW

    if e.cold_email and e.cold_email != "Not Available":
        return S.EnrichmentStatus.READY

    # Genuine, complete lead without any public contact.
    if genuine and source_ok and india_ok:
        return S.EnrichmentStatus.READY_WITHOUT_CONTACT

    return S.EnrichmentStatus.REVIEW


def _india_in_location(location: str) -> bool:
    m = re.compile(r"\bindia\b|\bbengaluru\b|\bmumbai\b|\bpune\b|\bnoida\b|\bgurugram\b|\bdelhi\b|\bhyderabad\b|\bchennai\b|\bkolkata\b|\bjaipur\b|\bahmedabad\b", re.IGNORECASE)
    return bool(m.search(location or ""))


class Enricher:
    """Enrich an already-ACCEPTED ClassifiedPost into an EnrichedLead."""

    def __init__(self, llm_provider=None, contact_provider=None):
        # Optional semantic LLM for accepted leads (spec §17). Without it the
        # enricher is fully deterministic. Never needed for vacancy judgement.
        # `contact_provider` is an OPTIONAL Phase-7 contact-discovery provider
        # (app.contact). Default None => NO external lookup, fully offline and
        # no-fabrication. Callers opt in explicitly.
        self.llm_provider = llm_provider
        self.contact_provider = contact_provider

    def enrich_from_classified(self, classified, llm_verdict=None, job_card_company: str = "Unclear", 
                           job_card_employment_type: str = "Unclear", job_card_experience: str = "Unclear") -> S.EnrichedLead:
        """Convenience wrapper: enrich a ClassifiedPost (plus optional verified
        LLM verdict fields) in one call. Never raises; a failure degrades to a
        REVIEW-flagged, honestly-empty EnrichedLead."""
        try:
            ver = llm_verdict or {}
            enriched = self.enrich(
                post_text=classified.text,
                source_link=classified.source_link,
                post_date=classified.post_date,
                author_name=classified.author_name,
                author_profile_url=classified.author_profile_url,
                job_metadata_company=classified.company,
                job_metadata_is_authoritative=(classified.company != "Unclear"),
                job_card_location=getattr(classified, "job_card_location", ""),
                classified_company=classified.company_name,
                classified_major_category=classified.major_category,
                classified_exact_role=classified.exact_role,
                classified_confidence=classified.confidence,
                classified_location=classified.location,
                classified_employment=classified.employment_type,
                classified_experience=classified.experience_requirement,
                classified_ctc=classified.ctc,
                classified_cold_email=classified.cold_email,
                classified_hiring_manager_name=classified.hiring_manager_name,
                classified_hiring_manager_linkedin=classified.hiring_manager_linkedin,
                classified_india_relevance=classified.india_relevance,
                classified_status=classified.status,
                llm_actual_role=getattr(ver, "actual_role", ""),
                llm_exact_role=getattr(ver, "exact_role", ""),
                llm_company=getattr(ver, "company", ""),
                llm_location=getattr(ver, "location", ""),
                llm_employment=getattr(ver, "employment_type", ""),
                llm_experience=getattr(ver, "experience_requirement", ""),
                llm_ctc=getattr(ver, "ctc", ""),
                llm_currency=getattr(ver, "currency", ""),
                llm_hiring_manager_name=getattr(ver, "hiring_manager_name", ""),
                llm_hiring_manager_linkedin=getattr(ver, "hiring_manager_linkedin", ""),
                llm_cold_email=getattr(ver, "cold_email", ""),
                llm_application_method=getattr(ver, "application_method", ""),
                llm_description=getattr(ver, "description", ""),
                contact_provider=self.contact_provider,
            )
            # QUESTION K (Phase 7): fold the LLM's per-field verbatim evidence
            # quotes into the audit trail so a lead stays fully auditable.
            for _label in (
                "evidence_vacancy", "evidence_role", "evidence_company",
                "evidence_location", "evidence_employment", "evidence_experience",
                "evidence_ctc", "evidence_hiring_manager", "evidence_email",
            ):
                q = getattr(ver, _label, "") or ""
                if q:
                    enriched.evidence_snippets.append(f"{_label}: {q}")
            return enriched
        except Exception as _e:  # enrichment must never crash the pipeline
            return S.EnrichedLead(
                source_link=getattr(classified, "source_link", ""),
                post_date=getattr(classified, "post_date", ""),
                company_name=classified.company_name,
                exact_role=classified.exact_role,
                major_category=classified.major_category,
                location=classified.location,
                employment_type=classified.employment_type,
                experience_required=classified.experience_requirement,
                ctc=classified.ctc,
                cold_email=classified.cold_email,
                hiring_manager_name=classified.hiring_manager_name,
                hiring_manager_linkedin=classified.hiring_manager_linkedin,
                verification_confidence=classified.confidence,
                verification_status=classified.status,
                enrichment_confidence=0.0,
                enrichment_status=S.EnrichmentStatus.REVIEW.value,
                enrichment_notes=["enrichment failed, degraded to source data"],
            )

    def enrich(self,
               post_text: str = "",
               source_link: str = "",
               post_date: str = "",
               author_name: str = "",
               author_profile_url: str = "",
               job_metadata_company: str = "",
               job_metadata_is_authoritative: bool = False,
               job_card_location: str = "",
               classified_company: str = "Unclear",
               classified_major_category: str = "Unclear",
               classified_exact_role: str = "Unclear",
               classified_confidence: float = 0.0,
               classified_location: str = "Not Specified",
               classified_employment: str = "Unclear",
               classified_experience: str = "Not Specified",
               classified_ctc: str = "Not Disclosed",
               classified_cold_email: str = "Not Available",
               classified_hiring_manager_name: str = "Unclear",
               classified_hiring_manager_linkedin: str = "Unclear",
               classified_india_relevance: str = "Unclear",
               classified_status: str = "New",
               llm_actual_role: str = "",
               llm_exact_role: str = "",
               llm_company: str = "",
               llm_location: str = "",
               llm_employment: str = "",
               llm_experience: str = "",
               llm_ctc: str = "",
               llm_currency: str = "",
                llm_hiring_manager_name: str = "",
                llm_hiring_manager_linkedin: str = "",
                llm_cold_email: str = "",
                llm_application_method: str = "",
                llm_description: str = "",
                verified_emails: Optional[Dict[str, str]] = None,
                contact_provider=None,
                ) -> S.EnrichedLead:
        """Enrich one verified (ACCEPTED) lead. Never fabricates."""
        text = post_text or ""
        if contact_provider is None:
            contact_provider = self.contact_provider

        # ── Company resolution (spec §1) ─────────────────────────────────────
        company_res = resolve_company(
            post_text=text,
            job_metadata_company=job_metadata_company,
            author_name=author_name,
            metadata_is_authoritative=job_metadata_is_authoritative,
        )
        # Prefer an explicit LLM-attributed company only when it is not a
        # default and there is no stronger deterministic attribution.
        if (company_res.company == "Unclear"
                and llm_company and llm_company not in ("Unclear", "")):
            company_res = S.CompanyResolution(
                company=llm_company,
                company_confidence=0.75,
                company_evidence="post_text",
                company_evidence_snippet="LLM attribution",
            )

        # ── Contact discovery (spec §24, Phase 7) ────────────────────────────
        # OPTIONAL: an explicitly-supplied contact-discovery provider may hand
        # in VERIFIED public contact evidence (official domain, public
        # careers/contact emails, publicly listed hiring contact) for the
        # resolved company. Fail-closed: any provider error / NOT_FOUND result
        # is ignored and never fabricates a contact or email. The default
        # provider is None => no external lookup at all (fully offline).
        discovered: Optional[ContactEvidence] = None
        provider_verified_emails: Dict[str, str] = {}
        provider_contact_name = None
        provider_contact_linkedin = "Not Available"
        provider_contact_evidence_url = ""
        discovered_status = ContactStatus.NOT_FOUND.value
        discovered_domain = "Not Available"
        discovered_domain_url = ""
        if contact_provider is not None and company_res.company not in ("Unclear", ""):
            try:
                discovered = contact_provider.discover(
                    company_res.company,
                    role=role_text_source(classified_exact_role, llm_exact_role, text),
                    source_text=text,
                    extras={"job_card_location": job_card_location},
                )
            except Exception as _e:  # fail closed: never fabricate on provider error
                discovered = None
            if discovered is not None:
                discovered_status = discovered.status.value
                if discovered.company_domain and discovered.company_domain != "Not Available":
                    discovered_domain = discovered.company_domain
                    discovered_domain_url = discovered.company_domain_evidence_url or ""
                for ve in discovered.verified_emails:
                    if ve.email and "@" in ve.email and "." in ve.email.split("@")[-1]:
                        # Do NOT trust provider emails that failed verification.
                        if ve.verified or discovered.status in (
                                ContactStatus.FOUND, ContactStatus.PARTIAL):
                            provider_verified_emails[ve.source or "contact_discovery"] = ve.email
                if discovered.contacts:
                    best_contact = max(discovered.contacts,
                                       key=lambda c: c.confidence)
                    # A provider contact hint is trusted ONLY when it carries
                    # explicit public evidence (evidence_url) — otherwise it is
                    # unattributed, possibly invented, and must not be used.
                    if best_contact.name and best_contact.name != "Unclear" \
                            and best_contact.evidence_url:
                        provider_contact_name = best_contact.name
                        provider_contact_evidence_url = best_contact.evidence_url
                        if best_contact.linkedin_url and \
                                best_contact.linkedin_url != "Not Available" and \
                                best_contact.linkedin_url.startswith("http"):
                            provider_contact_linkedin = best_contact.linkedin_url

        # Fold provider-verified emails in with any caller-supplied verified
        # emails (caller-supplied wins on conflict — it is the most deliberate).
        combined_verified_emails = dict(provider_verified_emails)
        if verified_emails:
            combined_verified_emails.update(verified_emails)

        # ── Exact role (spec §2) ─────────────────────────────────────────────
        role_res = resolve_exact_role(classified_exact_role, llm_actual_role,
                                      llm_exact_role, text)

        # ── Location (spec §3) ───────────────────────────────────────────────
        loc_res = normalize_location(classified_location, text, job_card_location)

        # ── Employment (spec §4) ─────────────────────────────────────────────
        emp_res = resolve_employment(text, classified_employment, llm_employment)

        # ── Experience (spec §5) ─────────────────────────────────────────────
        exp_res = resolve_experience(text, classified_experience, llm_experience)

        # ── CTC (spec §6) ────────────────────────────────────────────────────
        ctc_res = resolve_ctc(text, classified_ctc, llm_ctc, llm_currency)

        # ── Hiring manager + LinkedIn (spec §7/§8) ──────────────────────────
        # Only the LLM's attributed hiring manager (with explicit evidence) is
        # passed as Tier 1. The deterministic classifier's hiring manager is NOT
        # passed through — the contact resolver re-evaluates evidence tiers
        # independently to avoid false positives from author/company inference.
        # Provider hints are NOT used for hiring manager attribution.
        contact_res = resolve_contact(
            post_text=text,
            author_name=author_name,
            author_profile_url=author_profile_url,
            llm_hiring_manager_name=llm_hiring_manager_name,
            llm_hiring_manager_linkedin=llm_hiring_manager_linkedin,
            llm_application_method=llm_application_method,
            provider_hint_name=None,  # Provider hints only for email, not hiring manager
            provider_hint_linkedin="Not Available",
            provider_hint_evidence_url="",
        )

        # ── Email (spec §9) ──────────────────────────────────────────────────
        email_res = resolve_email(
            post_text=text,
            company_domain=company_res.company if company_res.company != "Unclear" else "",
            verified_emails=combined_verified_emails,
            provider_evidence=discovered,
            post_evidence_url=source_link,
            classified_cold_email=classified_cold_email,
        )

        # ── Description vs summary (spec §11) ───────────────────────────────
        description = re.sub(r"\s+", " ", text).strip() or classified_description_of(text)
        summary = llm_description.strip() if llm_description and llm_description != description else ""
        summary = summary[:600] if summary else ""

        # ── Phase 16: application links + key points (verbatim post literals,
        # recomputed here so enrichment carries the same deterministic values
        # as the sheet row even when called without a classified object) ──
        from app.extraction import (
            build_key_points as _build_key_points,
            extract_apply_link as _extract_apply_link,
            extract_google_form_url as _extract_google_form_url,
        )
        _form_url = _extract_google_form_url(text)
        _apply_link = _extract_apply_link(text, _form_url)
        _key_points = _build_key_points(text, company_res.company)

        # Build the enriched lead.
        enriched = S.EnrichedLead(
            source_link=source_link,
            post_date=post_date,
            post_description=description,
            llm_summary=summary,
            key_points=_key_points,
            apply_google_form=_form_url,
            apply_link=_apply_link,
            company_name=company_res.company,
            company_confidence=company_res.company_confidence,
            company_evidence=company_res.company_evidence.value,
            company_evidence_snippet=company_res.company_evidence_snippet,
            company_domain=discovered_domain,
            company_evidence_url=discovered_domain_url,
            major_category=classified_major_category,
            india_relevance=classified_india_relevance,
            exact_role=role_res.exact_role,
            role_confidence=role_res.role_confidence,
            role_evidence_snippet=role_res.role_evidence_snippet,
            location=loc_res.location,
            location_confidence=loc_res.location_confidence,
            is_remote=loc_res.is_remote,
            remote_is_india=loc_res.remote_is_india,
            employment_type=emp_res.employment_type,
            employment_confidence=emp_res.employment_confidence,
            experience_required=exp_res.experience_required,
            experience_confidence=exp_res.experience_confidence,
            ctc=ctc_res.ctc,
            ctc_confidence=ctc_res.ctc_confidence,
            hiring_manager_name=contact_res.name,
            hiring_manager_linkedin=contact_res.linkedin_url,
            hiring_manager_is_author=contact_res.is_author,
            hiring_manager_confidence=contact_res.confidence,
            hiring_manager_evidence=contact_res.evidence.value,
            hiring_manager_evidence_snippet=contact_res.evidence_snippet,
            hiring_manager_evidence_url=contact_res.evidence_url,
            cold_email=email_res.email,
            email_status=email_res.status.value,
            email_source=email_res.source,
            email_evidence_url=email_res.evidence_url,
            email_source_tier=email_res.source_tier,
            email_confidence=email_res.confidence,
            contact_discovery_status=discovered_status,
            verification_confidence=classified_confidence,
            verification_status=classified_status,
            evidence_snippets=_collect_evidence_snippets(company_res, role_res, loc_res,
                                                         emp_res, exp_res, ctc_res, contact_res),
        )

        if summary:
            # Phase 16 fix (baseline §9.1 cosmetic bug): evidence_snippets is
            # List[str] — the old tuple ("llm_summary", …) tripped Pydantic
            # serializer warnings on every ACCEPT. A plain string now.
            enriched.evidence_snippets.append(f"llm_summary: {summary[:120]}")

        enriched.enrichment_confidence = _enrichment_confidence(enriched)
        enriched.data_quality_score = _data_quality_score(enriched)
        enriched.enrichment_status = _enrichment_status(enriched).value
        return enriched


def _collect_evidence_snippets(*resolutions) -> list:
    out = []
    for r in resolutions:
        s = getattr(r, "evidence_snippet", "") or getattr(r, "company_evidence_snippet", "")
        if s:
            out.append(s)
    return out[:8]


def classified_description_of(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "")).strip()
    return s[:600]


def role_text_source(classified_exact_role: str, llm_exact_role: str, post_text: str) -> str:
    """Best available exact-role text for passing to a contact-discovery
    provider. Never raw user input only; prefers the curated role when present.
    """
    for cand in (llm_exact_role, classified_exact_role):
        if cand and cand not in ("Unclear", "") and cand not in _NOT_COMPANY_KEYWORDS:
            return cand
    return (post_text or "")[:200]
