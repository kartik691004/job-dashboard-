"""
production_gate.py — Phase 24 direct-company + verified-HM production gate.

Precision-first, additive layer between verification/enrichment and the Sheet.
It NEVER promotes (REVIEW/REJECT stay untouched) and NEVER fabricates: it only
answers "may this ACCEPT reach production?" using already-resolved fields plus
read-only re-validation of their evidence tiers.

Mandatory production requirements (§7):
  1. gate decision ACCEPT with an evaluated LLM (never UNAVAILABLE/NOT_RUN/ERROR)
  2. confidence >= 0.60 (< 0.60 → REJECT disposition, never written)
  3. company identifiable AND evidence-backed (EXPLICIT text/job-card evidence
     matching the stored company; INFERRED/AMBIGUOUS/NOT_FOUND → HOLD)
  4. verified hiring manager (stored name + qualifying hiring-side evidence;
     missing/ambiguous → HOLD, never ACCEPT)
  5. poster/source policy (advertising pages and aggregators → REJECT; unknown
     or weakly-evidenced sources → HOLD; direct/HM/employee/recruiter only with
     the evidence §3–§4 demand)
  6. exact role present and specific (vague/multi-role → HOLD)
  7. India eligibility re-asserted (defence in depth; upstream gates unchanged)

Dispositions: ELIGIBLE (may be written) | HOLD (human review, never New) |
REJECT (structural: advertising/aggregator source or confidence < 0.60).

Internal-only fields (poster_type, resolutions) never touch the 23-col Sheet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.config import AGGREGATOR_PATTERNS
from app.enrichment.company_resolver import resolve as resolve_employer
from app.enrichment.schemas import CompanyEvidence
from app.llm.schemas import INDIA_PASS_VALUES, LLM_NON_EVALUATED
from app.resolution import (
    AGENCY_POSTER_PATTERNS,
    ResolutionState,
    intermediary_evidence,
    resolve_hm,
    resolve_role,
    undisclosed_evidence,
)

# ── Policy constants ──────────────────────────────────────────────────────
MIN_PRODUCTION_CONFIDENCE = 0.60
MAX_DESCRIPTION_WORDS = 600

ELIGIBLE = "ELIGIBLE"
HOLD = "HOLD"
REJECT = "REJECT"

# ── Phase 24.1: production-tab routing ─────────────────────────────────────
# Mutually exclusive destinations. Routing runs AFTER the Phase 24 gate: only
# ELIGIBLE leads route; HOLD/REJECT never reach either production tab.
MAIN_PRODUCTION = "MAIN_PRODUCTION"  # "LinkedIn Hiring Leads" tab
DATA_PRODUCTION = "DATA_PRODUCTION"  # "Data Analyst & Data Scientist" tab

# Categories routed to the data tab (Phase 17/20 taxonomy, exact values).
DATA_CATEGORIES = frozenset({"Data Analyst", "Data Scientist"})
# Categories routed to the main tab (Phase 25.2 HARD invariant). These are
# the ONLY non-data production categories in the pipeline taxonomy
# (classifier + LeadVerdict coercion admit exactly FO/CoS/DA/DS/None).
# Role titles such as Founder Associate, CEO Office, Strategy, BizOps,
# Growth, Generalist, EIR, Special Projects or VC live in `exact_role`
# under one of these two categories — they are NOT category values and must
# never be invented as such. Anything else is unroutable → HOLD.
MAIN_PRODUCTION_CATEGORIES = frozenset({"Founder's Office", "Chief of Staff"})
# Stored values that carry no routable category.
UNROUTABLE_CATEGORIES = frozenset({"", "Unclear", "N/A", "None", "unknown", "none"})


class PosterType:
    DIRECT_COMPANY = "DIRECT_COMPANY"
    HIRING_MANAGER = "HIRING_MANAGER"
    EMPLOYEE = "EMPLOYEE"
    RECRUITER_FOR_COMPANY = "RECRUITER_FOR_COMPANY"
    AGENCY = "AGENCY"
    JOB_ADVERTISING_PAGE = "JOB_ADVERTISING_PAGE"
    AGGREGATOR = "AGGREGATOR"
    UNKNOWN = "UNKNOWN"


# Structural rejects: these posters can never yield a production lead.
REJECT_POSTERS = frozenset({
    PosterType.JOB_ADVERTISING_PAGE,
    PosterType.AGGREGATOR,
})

# Author names shaped like job-ad brands. First-token/generic-modifier rule
# keeps real surnames ("Steve Jobs") out: the hit must lead the name or ride
# with a generic modifier (india/official/page/alerts/updates/hub/...).
_AD_TOKENS = (
    r"jobs?", r"hiring", r"careers?", r"naukri", r"foundit", r"shine",
    r"timesjobs", r"freshersworld", r"iimjobs", r"cutshort", r"\bapna\b",
    r"placements?", r"vacanc(?:y|ies)", r"alerts?", r"updates?",
    r"referrals?", r"job\s*search", r"hiring\s*hub", r"job\s*hub",
    r"talent\s*hub", r"pagaar", r"careerbee",
)
_AD_RE = re.compile("|".join(_AD_TOKENS), re.IGNORECASE)
_GENERIC_MODIFIERS = re.compile(
    r"\b(india|indian|official|page|hub|alerts?|updates?|portal|point|zone|"
    r"adda|bhaiya|24x7|for\s+you|jobs?|careers?|hiring)\b",
    re.IGNORECASE,
)

# First-person hiring voice (singular = the poster hires; plural = company voice).
_SINGULAR_HIRING = re.compile(
    r"\bi(?:['\u2019]?m|\s+am)\s+(?:hiring|recruiting)\b|"
    r"\bmy\s+team\s+is\s+hiring\b|"
    r"\bcontact\s+me\s+(?:to\s+)?apply\b|"
    r"\breach\s+out\s+to\s+me\b|"
    r"\bdm\s+me\b|"
    r"\bi(?:['\u2019]?m|\s+am)\s+the\s+(?:founder|ceo|co-founder|head)\b",
    re.IGNORECASE,
)
_PLURAL_HIRING = re.compile(
    r"\bwe(?:['\u2019]?re|\s+are)\s+(?:hiring|recruiting|looking\s+for)\b|"
    r"\bjoin\s+(?:us|our\s+team)\b",
    re.IGNORECASE,
)
_RECRUITER_PERSONA = re.compile(
    r"\b(?:recruit(?:er|ing|ment)|talent\s+(?:acquisition|team|partner)|"
    r"hiring\s+manager|hr\s+(?:manager|executive)|people\s+team|"
    r"talent\s+team|human\s+resources)\b",
    re.IGNORECASE,
)

# Vague role placeholders that can never satisfy the exact-role gate.
_VAGUE_ROLE_RES = (
    r"multiple\s+(?:openings?|roles?|positions?|vacancies|jobs?)",
    r"various\s+(?:roles?|positions?|openings?|jobs?)",
    r"several\s+(?:roles?|positions?|openings?)",
    r"\bhiring\s+now\b",
    r"management\s+roles?",
    r"open\s+roles?",
    r"job\s+openings?",
    r"multiple\s+open\s+roles?",
)
_VAGUE_ROLE_RE = re.compile("|".join(_VAGUE_ROLE_RES), re.IGNORECASE)

_MISSING_COMPANY = {"", "unclear", "n/a", "na", "none", "unknown", "undisclosed",
                    "our client", "confidential client", "client undisclosed",
                    "confidential", "stealth"}
_MISSING_ROLE = {"", "unclear", "n/a", "na", "none", "unknown"}
_MISSING_HM = {"", "unclear", "n/a", "na", "none", "unknown"}


@dataclass
class ProductionGateResult:
    eligible: bool
    disposition: str  # ELIGIBLE | HOLD | REJECT
    reasons: List[str] = field(default_factory=list)
    poster_type: str = PosterType.UNKNOWN
    company_resolution: str = ""
    company_value: str = ""
    hm_resolution: str = ""
    hm_value: str = ""
    checks: Dict[str, Any] = field(default_factory=dict)


# ── Helpers ───────────────────────────────────────────────────────────────

def _is_person_profile(url: str) -> bool:
    u = (url or "").lower()
    return "/in/" in u and "/company/" not in u


def _is_company_page(url: str) -> bool:
    return "/company/" in (url or "").lower()


def _tokens(s: str) -> List[str]:
    return [w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if w]


def _names_match(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return (all(t in tb for t in ta) or all(t in ta for t in tb))


def _advertising_poster(author_name: str, author_profile_url: str) -> str:
    """Verbatim evidence when the author is shaped like a job-ad brand, else ''.

    First-token-or-modifier rule: "CareerJobs India" / "Naukri" hit, while a
    trailing surname ("Steve Jobs") with a person profile does not.
    """
    name = (author_name or "").strip()
    if not name:
        return ""
    m = _AD_RE.search(name)
    if not m:
        return ""
    first_token = re.split(r"\s+", name, maxsplit=1)[0]
    leads = bool(_AD_RE.search(first_token))
    if leads or _is_company_page(author_profile_url):
        return "ad-shaped poster: %s" % name[:80]
    # Person profiles are never ad-flagged on a modifier alone (protects real
    # surnames such as "Steve Jobs"); missing/unknown profiles with a generic
    # modifier still count as ad-shaped.
    if not _is_person_profile(author_profile_url) \
            and bool(_GENERIC_MODIFIERS.search(name)):
        return "ad-shaped poster: %s" % name[:80]
    return ""


def _agency_poster(author_name: str, text: str) -> str:
    """Evidence when the poster is agency-shaped (name or text signals)."""
    for pat in AGENCY_POSTER_PATTERNS:
        m = re.search(pat, author_name or "", re.IGNORECASE)
        if m:
            return "agency-shaped poster: %s" % (author_name or "").strip()[:80]
    hit = intermediary_evidence(text or "", "")
    if hit and "client" in hit.lower():
        return hit
    return ""


def _aggregator_text(text: str) -> str:
    for pat in AGGREGATOR_PATTERNS:
        m = re.search(pat, text or "", re.IGNORECASE)
        if m:
            return m.group(0).strip()[:120]
    return ""


# ── Poster classification ─────────────────────────────────────────────────

def classify_poster(author_name: str = "", author_profile_url: str = "",
                    text: str = "", company: str = "",
                    hm_name: str = "", hm_evidence: str = "") -> Dict[str, str]:
    """Classify the post's source. Returns {poster_type, evidence}.

    Conservative: anything unrecognized is UNKNOWN (→ HOLD downstream), never
    assumed direct. A company name appearing in third-party ad copy never
    promotes the poster by itself.
    """
    text = text or ""
    if _aggregator_text(text):
        return {"poster_type": PosterType.AGGREGATOR,
                "evidence": "aggregator-shaped post: %s" % _aggregator_text(text)}
    ad = _advertising_poster(author_name, author_profile_url)
    if ad:
        return {"poster_type": PosterType.JOB_ADVERTISING_PAGE, "evidence": ad}
    agency = _agency_poster(author_name or "", text)
    person = _is_person_profile(author_profile_url or "")
    company_page = _is_company_page(author_profile_url or "")
    hm_is_author = (hm_name and hm_name not in _MISSING_HM
                    and _names_match(hm_name, author_name or ""))

    if company_page:
        if company and company.lower() not in _MISSING_COMPANY \
                and _names_match(author_name or "", company):
            return {"poster_type": PosterType.DIRECT_COMPANY,
                    "evidence": "company page matches hiring company"}
        if agency:
            return {"poster_type": PosterType.AGENCY, "evidence": agency}
        return {"poster_type": PosterType.JOB_ADVERTISING_PAGE,
                "evidence": "company page posting another employer's vacancy"}
    if agency:
        return {"poster_type": PosterType.AGENCY, "evidence": agency}
    if person:
        if hm_is_author and (hm_evidence or "") and _SINGULAR_HIRING.search(text):
            return {"poster_type": PosterType.HIRING_MANAGER,
                    "evidence": "author is the evidenced hiring person"}
        if _RECRUITER_PERSONA.search(text):
            return {"poster_type": PosterType.RECRUITER_FOR_COMPANY,
                    "evidence": "recruiter persona handling the vacancy"}
        if _PLURAL_HIRING.search(text) or _SINGULAR_HIRING.search(text):
            return {"poster_type": PosterType.EMPLOYEE,
                    "evidence": "employee-shaped hiring post"}
    return {"poster_type": PosterType.UNKNOWN, "evidence": "unrecognized source"}


# ── The gate ──────────────────────────────────────────────────────────────

def _val(merged: Any, *names: str, default: str = "") -> str:
    for n in names:
        v = getattr(merged, n, None)
        if v not in (None, ""):
            return v
    return default


def final_category(merged: Any, gate: Any) -> str:
    """Final validated category for routing (no keyword matching).

    Prefers the LLM-resolved verdict category when the model evaluated and
    resolved one; falls back to the stored (deterministic) category. This
    preserves existing conflict resolution: an evaluated verdict wins, an
    unevaluated/None verdict keeps the pipeline value.
    """
    try:
        llm_status = getattr(gate, "llm_status", "ERROR")
        verdict = getattr(gate, "verdict", None)
        vcat = getattr(verdict, "major_category", "") or ""
        if llm_status not in LLM_NON_EVALUATED and vcat not in UNROUTABLE_CATEGORIES:
            return vcat
    except Exception:
        pass
    return _val(merged, "major_category", default="None")


def route_category(major_category: str) -> str:
    """Route one validated category to exactly one tab (or HOLD).

    Phase 25.2 HARD invariant (fail closed, never default-MAIN):
    Data Analyst / Data Scientist → DATA_PRODUCTION; Founder's Office /
    Chief of Staff → MAIN_PRODUCTION; missing, ambiguous, conflicting,
    unsupported, or otherwise unroutable → HOLD (never written to either
    tab). A post can therefore never appear in both tabs, and a data lead
    can never enter Main (nor a non-data lead Data).
    """
    cat = (major_category or "").strip()
    if cat in DATA_CATEGORIES:
        return DATA_PRODUCTION
    if cat in MAIN_PRODUCTION_CATEGORIES:
        return MAIN_PRODUCTION
    return HOLD


@dataclass
class RouteResult:
    destination: str  # MAIN_PRODUCTION | DATA_PRODUCTION | HOLD | REJECT
    gate_result: "ProductionGateResult | None" = None
    category: str = ""
    reasons: List[str] = field(default_factory=list)


def route_production_post(merged: Any, gate: Any) -> RouteResult:
    """Route one lead to exactly one production tab (never both, never neither
    when eligible). Runs the full Phase 24 gate first: ineligible leads keep
    their HOLD/REJECT disposition and stay out of both tabs. REVIEW/REJECT
    decisions and unevaluated LLMs never route.
    """
    try:
        decision = getattr(gate, "decision", "REVIEW")
        if decision == "REJECT":
            return RouteResult(REJECT, None, "",
                               ["model REJECT never reaches production"])
        if decision != "ACCEPT":
            return RouteResult(HOLD, None, "",
                               ["not an ACCEPT (no promotion)"])
        res = evaluate(merged, gate)
    except Exception as e:  # fail closed like the gate itself
        return RouteResult(HOLD, None, "",
                           ["routing error; held: %s" % type(e).__name__])
    if not res.eligible:
        return RouteResult(res.disposition, res, "",
                           list(res.reasons))
    cat = final_category(merged, gate)
    dest = route_category(cat)
    if dest == HOLD:
        return RouteResult(HOLD, res, cat,
                           ["eligible but category unroutable (%s); held" % cat])
    return RouteResult(dest, res, cat,
                       ["routed to %s by category %s" % (dest, cat)])


def evaluate(merged: Any, gate: Any) -> ProductionGateResult:
    """Decide production eligibility for one ACCEPT-path lead.

    `merged`: ClassifiedPost after LLM fold (source_link, text, author_*,
    company_name, exact_role, location, employment_type, experience_requirement,
    hiring_manager_*, apply_*, description, key_points, india_relevance,
    job_card_*). `gate`: GateResult (decision, llm_status, confidence,
    llm_confidence). Pure: no network, no mutation, never raises.
    """
    reasons: List[str] = []
    checks: Dict[str, Any] = {}
    hold: List[str] = []
    reject: List[str] = []

    try:
        text = getattr(merged, "text", "") or ""
        author = getattr(merged, "author_name", "") or ""
        profile = getattr(merged, "author_profile_url", "") or ""
        job_card_company = getattr(merged, "job_card_company", "") or ""
        company = _val(merged, "company_name")
        role = _val(merged, "exact_role")
        category = _val(merged, "major_category", default="None")
        hm_name = _val(merged, "hiring_manager_name")
        hm_ev = _val(merged, "hiring_manager_evidence")
        india = _val(merged, "india_relevance", default="Unclear")
        description = _val(merged, "description")

        decision = getattr(gate, "decision", "REVIEW")
        llm_status = getattr(gate, "llm_status", "ERROR")
        confidence = getattr(gate, "confidence", 0.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        llm_conf = getattr(gate, "llm_confidence", None)

        # 0. Only ACCEPT-path leads are candidates; never promote others.
        checks["is_accept"] = (decision == "ACCEPT")
        if decision != "ACCEPT":
            return ProductionGateResult(False, HOLD, ["not an ACCEPT (no promotion)"],
                                        PosterType.UNKNOWN, "", company, "", hm_name,
                                        checks)

        # 1. The model must have evaluated (null confidence ⇒ never ACCEPT).
        checks["llm_evaluated"] = llm_status not in LLM_NON_EVALUATED and llm_conf is not None
        if not checks["llm_evaluated"]:
            return ProductionGateResult(False, HOLD,
                                        ["LLM never evaluated (%s); held, never ACCEPT" % llm_status],
                                        PosterType.UNKNOWN, "", company, "", hm_name, checks)

        # 2. Confidence floor: < 0.60 is structural REJECT.
        checks["confidence"] = round(confidence, 4)
        if confidence < MIN_PRODUCTION_CONFIDENCE:
            return ProductionGateResult(False, REJECT,
                                        ["confidence %.2f below 0.60 production bar" % confidence],
                                        PosterType.UNKNOWN, "", company, "", hm_name, checks)

        # 3. Poster/source policy first: ads and aggregators can never pass,
        #    and a company name inside third-party copy never suffices alone.
        poster = classify_poster(author, profile, text, company, hm_name, hm_ev)
        checks["poster_type"] = poster["poster_type"]
        checks["poster_evidence"] = poster["evidence"]
        if poster["poster_type"] in REJECT_POSTERS:
            return ProductionGateResult(False, REJECT,
                                        ["poster %s: %s" % (poster["poster_type"],
                                                            poster["evidence"])],
                                        poster["poster_type"], "", company, "", hm_name,
                                        checks)

        # 4. Company: must resolve from evidence (production enrichment tiers:
        # POST_TEXT / JOB_METADATA) AND match the stored company. Weak,
        # inferred, ambiguous, or missing attribution ⇒ HOLD. A company name
        # inside third-party copy never suffices without the poster policy (§3).
        authoritative = bool((job_card_company or "").strip()
                             and job_card_company.strip().lower() != "unclear")
        emp = resolve_employer(text, job_card_company, author, "",
                               metadata_is_authoritative=authoritative)
        checks["company_resolution"] = emp.company_evidence.value
        checks["company_evidence"] = emp.company_evidence_snippet[:160]
        comp_ok = (
            company.lower() not in _MISSING_COMPANY
            and emp.company_evidence in (CompanyEvidence.POST_TEXT,
                                         CompanyEvidence.JOB_METADATA)
            and _names_match(emp.company, company)
        )
        checks["company_ok"] = comp_ok
        if not comp_ok:
            hold.append("company not evidence-backed (stored=%s, evidence=%s)"
                        % (company or "Unclear", emp.company_evidence.value))

        # 5. Hiring manager: stored name + qualifying hiring-side evidence tier.
        hm_res = resolve_hm(hm_name if hm_name.lower() not in _MISSING_HM else "",
                            hm_ev, author, profile, text)
        checks["hm_resolution"] = hm_res.resolution
        checks["hm_evidence"] = hm_res.evidence[:160]
        hm_ok = (
            hm_name.lower() not in _MISSING_HM
            and hm_res.resolution in (ResolutionState.EXPLICIT,
                                      ResolutionState.INFERRED_WITH_STRONG_EVIDENCE)
        )
        checks["hm_ok"] = hm_ok
        if not hm_ok:
            hold.append("hiring manager unverified (name=%s, resolution=%s)"
                        % (hm_name or "Unclear", hm_res.resolution))

        # 6. Poster-specific evidence bars (§3–§4).
        pt = poster["poster_type"]
        if pt == PosterType.UNKNOWN:
            hold.append("unrecognized source (%s); held" % poster["evidence"])
        elif pt == PosterType.AGENCY:
            if undisclosed_evidence(text):
                hold.append("agency post with undisclosed client")
            elif not (comp_ok and hm_ok):
                hold.append("agency post lacks explicit company + hiring relationship")
        elif pt == PosterType.RECRUITER_FOR_COMPANY:
            if undisclosed_evidence(text):
                hold.append("recruiter post with undisclosed client")
            elif not (comp_ok and hm_ok):
                hold.append("recruiter post lacks explicit company + hiring evidence")
            elif hm_res.resolution != ResolutionState.EXPLICIT:
                hold.append("recruiter hiring evidence not explicit")
        elif pt == PosterType.EMPLOYEE:
            if not comp_ok:
                hold.append("employee post lacks explicit company evidence")
        # DIRECT_COMPANY / HIRING_MANAGER need only the standard gates above.

        # 7. Exact role: present, specific, vacancy-shaped.
        role_missing = role.strip().lower() in _MISSING_ROLE or category == "None"
        role_vague = bool(_VAGUE_ROLE_RE.search(role) or _VAGUE_ROLE_RE.search(text[:400]))
        role_res = resolve_role(role, text)
        role_ok = (not role_missing and not role_vague
                   and role_res.resolution != ResolutionState.AMBIGUOUS)
        checks["role_ok"] = role_ok
        checks["role_resolution"] = role_res.resolution
        if not role_ok:
            hold.append("exact role missing/vague (role=%s)" % (role or "Unclear"))

        # 8. India eligibility re-asserted (upstream gates unchanged).
        checks["india_ok"] = india in INDIA_PASS_VALUES
        if not checks["india_ok"]:
            hold.append("India eligibility not established (%s)" % india)

        # 9. Description cap (good-to-have hygiene; never mutates).
        words = len((description or "").split())
        checks["description_words"] = words
        if words > MAX_DESCRIPTION_WORDS:
            hold.append("description exceeds 600-word cap")

        if hold:
            return ProductionGateResult(False, HOLD, hold, pt,
                                        emp.company_evidence.value, company,
                                        hm_res.resolution, hm_name, checks)
        if reject:
            return ProductionGateResult(False, REJECT, reject, pt,
                                        emp.company_evidence.value, company,
                                        hm_res.resolution, hm_name, checks)
        reasons = ["direct source=%s; company=%s (%s); hm=%s (%s); role=%s; conf=%.2f"
                   % (pt, company, emp.company_evidence.value, hm_name,
                      hm_res.resolution, role, confidence)]
        return ProductionGateResult(True, ELIGIBLE, reasons, pt,
                                    emp.company_evidence.value, company,
                                    hm_res.resolution, hm_name, checks)
    except Exception as e:  # fail closed: gate trouble ⇒ hold, never write
        return ProductionGateResult(False, HOLD,
                                    ["production-gate error; held: %s" % type(e).__name__],
                                    PosterType.UNKNOWN, "", "", "", "", checks)
