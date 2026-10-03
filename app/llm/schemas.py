"""
schemas.py — validated LLM output.

The model behind Groq returns free text; the ONLY thing the pipeline trusts is
a LeadVerdict that survived Pydantic validation. Every field degrades to a safe
default instead of raising: a malformed value must produce a REVIEW-able
verdict, never a pipeline crash. Safe defaults mirror the sheet conventions
("Unclear", "Not Available", "Not Specified", "Not Disclosed").
"""
from typing import List, Literal

from pydantic import BaseModel, Field, field_validator

Decision = Literal["ACCEPT", "REVIEW", "REJECT"]

# ── Phase 18A: LLM execution status (INTERNAL ONLY, never a Sheet column) ────
# A failed/unavailable LLM call must NEVER be represented as confidence 0.00
# (which reads as a real model rejection) or as any kind of rejection.
# Terminal model evaluations reuse the decision vocabulary (SUCCESS maps to
# ACCEPT, plus REJECT/REVIEW); non-evaluations are explicit:
#   NOT_RUN     — provider disabled; verify() never attempted a call
#   SUCCESS     — model evaluated and the gate ACCEPTed
#   REJECT      — model evaluated and the gate REJECTed
#   REVIEW      — model evaluated and the gate held for review
#   UNAVAILABLE — transport/schema failure after retry; safe to recover later
#   ERROR       — unexpected exception path; needs inspection, not recovery
LLMStatus = Literal["NOT_RUN", "SUCCESS", "REJECT", "REVIEW", "UNAVAILABLE", "ERROR"]

# Statuses where the model never evaluated the post: confidence MUST be read
# as null (see GateResult.llm_confidence), never as 0.00, and the outcome
# MUST NOT be treated as a rejection or a quality signal.
LLM_NON_EVALUATED = ("NOT_RUN", "UNAVAILABLE", "ERROR")

MAJOR_CATEGORIES = {
    "Founder's Office", "Chief of Staff",
    # Phase 17: explicit lead categories for data roles. Existing values are
    # pinned — no rename, so every stored lead and frozen test keeps passing.
    "Data Analyst", "Data Scientist", "None",
}
INDIA_RELEVANCE_VALUES = {
    "India", "Remote - India", "Indian Company", "India + Global",
    "Unclear", "Not India",
}
# india_relevance values that satisfy the hard India gate.
INDIA_PASS_VALUES = {"India", "Remote - India", "Indian Company", "India + Global"}
# Full-time is the product. Everything else is held for review (or rejected).
EMPLOYMENT_TYPES = {
    "Full-time", "Contract", "Internship",
    "Part-time", "Freelance", "Temporary", "Unclear",
}
# Employment types that must never be auto-accepted; held for human review.
# "Unclear" is excluded because a lead may be ACCEPT when all essential quality
# conditions pass even if employment type is Unclear (no explicit evidence, not
# evidence of non-full-time). Explicit types (Contract/Part-time/etc.) are still
# held for review.
NON_ACCEPT_EMPLOYMENT = {
    "Contract", "Part-time", "Freelance", "Temporary",
}

TEXT_FIELDS = (
    "exact_role", "company", "location", "experience_requirement",
    "ctc", "currency", "hiring_manager_name", "hiring_manager_linkedin",
    "hiring_manager_evidence",
    "cold_email", "application_method", "apply_google_form", "apply_link",
    "description", "reason",
)

# QUESTION K (LLM quality protocol, Phase 7): per-field evidence quotes. Each
# field must hold a SHORT QUOTE from the post that supports the corresponding
# conclusion, verbatim when possible (never a paraphrase, never a guess). Empty
# string means "no evidence found" — never fill these with invented text.
EVIDENCE_FIELDS = (
    "evidence_vacancy",     # "real current vacancy" conclusion
    "evidence_role",        # exact advertised role
    "evidence_company",     # company attribution
    "evidence_location",    # India-relevant location
    "evidence_employment",  # employment type (full-time etc.)
    "evidence_experience",  # experience requirement
    "evidence_ctc",         # compensation
    "evidence_hiring_manager",  # hiring responsibility
    "evidence_email",       # public contact email
)


class LeadVerdict(BaseModel):
    """One candidate's semantic evaluation, exactly as specified in the plan."""

    decision: Decision = "REVIEW"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_current_job: bool = False
    is_genuine_hiring: bool = False
    is_target_role: bool = False
    # ── Data-quality signals (data-quality correction spec) ────────────────────
    # True when the post primarily distributes many jobs rather than announce one.
    is_aggregator: bool = False
    # True when the post explicitly states it is NOT hiring the target role.
    has_negation: bool = False
    # True when the target role appears only as a required background/experience.
    is_experience_requirement_only: bool = False
    # The REAL advertised job title, distinct from a keyword match. Used by the
    # Python gate to veto an LLM ACCEPT whose actual role is non-target.
    actual_role: str = "Unclear"
    major_category: str = "None"
    exact_role: str = "Unclear"
    company: str = "Unclear"
    india_relevance: str = "Unclear"
    location: str = "Not Specified"
    employment_type: str = "Unclear"
    experience_requirement: str = "Not Specified"
    ctc: str = "Not Disclosed"
    currency: str = ""
    hiring_manager_name: str = "Unclear"
    hiring_manager_linkedin: str = "Unclear"
    # Phase 16: verbatim quote backing the hiring-manager attribution ("" =
    # no evidence). Never paraphrased, never invented.
    hiring_manager_evidence: str = ""
    cold_email: str = "Not Available"
    application_method: str = ""
    # Phase 16: exact literal Google Form URL from the post ("" when none —
    # never fabricated, shortlinks never relabeled as forms).
    apply_google_form: str = ""
    # Phase 16: best literal application URL ("" when none).
    apply_link: str = ""
    description: str = ""
    reason: str = ""
    evidence: List[str] = Field(default_factory=list)
    # ── Q-K: per-field evidence quotes (Phase 7 LLM quality protocol) ─────────
    # Short verbatim quotes supporting the corresponding conclusion; "" = none.
    evidence_vacancy: str = ""
    evidence_role: str = ""
    evidence_company: str = ""
    evidence_location: str = ""
    evidence_employment: str = ""
    evidence_experience: str = ""
    evidence_ctc: str = ""
    evidence_hiring_manager: str = ""
    evidence_email: str = ""

    # ── Coercion: unknown values fall back to safe defaults ──────────────────
    @field_validator("major_category", mode="before")
    @classmethod
    def _valid_major(cls, v):
        return v if isinstance(v, str) and v in MAJOR_CATEGORIES else "None"

    @field_validator("india_relevance", mode="before")
    @classmethod
    def _valid_india(cls, v):
        return v if isinstance(v, str) and v in INDIA_RELEVANCE_VALUES else "Unclear"

    @field_validator("employment_type", mode="before")
    @classmethod
    def _valid_employment(cls, v):
        return v if isinstance(v, str) and v in EMPLOYMENT_TYPES else "Unclear"

    @field_validator("decision", mode="before")
    @classmethod
    def _upper_decision(cls, v):
        return str(v).strip().upper() if isinstance(v, str) else v

    @field_validator(*TEXT_FIELDS, mode="before")
    @classmethod
    def _str_coerce(cls, v):
        return v.strip() if isinstance(v, str) else ""

    @field_validator(*EVIDENCE_FIELDS, mode="before")
    @classmethod
    def _evidence_quote_coerce(cls, v):
        """Coerce per-field evidence to a short, single-line verbatim quote.

        Anything that is not a string becomes "". Deliberately does NOT invent
        or paraphrase: the LLM is instructed to quote the post verbatim, and a
        non-quote value degrades to "" (audited as missing evidence), never to a
        fabricated quote.
        """
        if not isinstance(v, str):
            return ""
        s = " ".join(v.split())
        return s[:300]

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, v):
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.0

    @field_validator("evidence", mode="before")
    @classmethod
    def _list_of_str(cls, v):
        if isinstance(v, list):
            return [str(x) for x in v]
        return []


class GateResult(BaseModel):
    """Final pipeline verdict: LeadVerdict + Python-side hard-gate outcome."""

    verdict: LeadVerdict
    decision: Decision            # after hard gates — the ONLY authoritative decision
    status: str                   # "New" | "Review" | "" (rejected rows are never written)
    reasons: List[str] = Field(default_factory=list)
    llm_called: bool = True       # False when the provider was unavailable/disabled
    # ── Phase 18A: execution status (INTERNAL ONLY — never Sheet columns) ─────
    # llm_status tells whether the model actually evaluated the post. When it
    # did not (NOT_RUN/UNAVAILABLE/ERROR), llm_confidence is None and the
    # outcome must not be read as a rejection, a 0.00 score, or a quality
    # signal. `confidence` is kept byte-compatible for the Sheet path; new
    # consumers must branch on llm_status / llm_confidence instead.
    llm_status: LLMStatus = "SUCCESS"
    llm_error: str = ""           # sanitized provider/error reason, "" when none
    llm_provider: str = ""        # provider class name that ran, "" when none ran

    @property
    def confidence(self) -> float:
        return self.verdict.confidence

    @property
    def llm_confidence(self) -> float | None:
        """Model confidence, or None when the model never evaluated the post."""
        if self.llm_status in LLM_NON_EVALUATED:
            return None
        return self.verdict.confidence
