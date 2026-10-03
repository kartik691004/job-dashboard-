"""
schemas.py — data contracts for the Phase 6 LEAD ENRICHMENT layer.

The enrichment layer runs AFTER a lead has already passed the existing
verification gates (deterministic classifier -> Groq verifier -> hard gates ->
ACCEPT). It is responsible ONLY for extraction / resolution / contact
discovery / normalization / evidence / completeness. It never decides whether
a post is a genuine vacancy, never fabricates information, and never changes
the verification decision.

Every field degrades to a safe, honest default ("Unclear", "Not Specified",
"Not Disclosed", "Not Available") rather than a guessed value. "Not Available"
for a missing email is a SUCCESSFUL safe outcome; a fabricated email is a
FAILURE.
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class CompanyEvidence(str, Enum):
    """Where the employer attribution came from (spec §1)."""

    POST_TEXT = "post_text"
    JOB_METADATA = "job_metadata"
    AUTHOR_CONTEXT = "author_context"
    EXTERNAL_SOURCE = "external_source"
    UNCLEAR = "unclear"


class HiringManagerEvidence(str, Enum):
    """Where the hiring-manager attribution came from (spec §7)."""

    EXPLICIT_HIRING_MANAGER = "explicit_hiring_manager"
    POSTER_IS_HIRING = "poster_is_hiring"
    RECRUITER_HANDLING_ROLE = "recruiter_handling_role"
    FOUNDER_LEADERSHIP = "founder_leadership"
    AUTHOR_CONTEXT = "author_context"
    UNCLEAR = "unclear"


class EmailStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    NOT_PUBLIC = "NOT_PUBLIC"


class EnrichmentStatus(str, Enum):
    """Final quality gate after enrichment (spec §14)."""

    READY = "READY"
    READY_WITHOUT_CONTACT = "READY_WITHOUT_CONTACT"
    REVIEW = "REVIEW"


class EmploymentType(str, Enum):
    FULL_TIME = "Full-time"
    INTERNSHIP = "Internship"
    PART_TIME = "Part-time"
    CONTRACT = "Contract"
    FREELANCE = "Freelance"
    TEMPORARY = "Temporary"
    UNCLEAR = "Unclear"


class LocationPreset(str, Enum):
    # Normalised well-known city spellings (spec §3).
    BENGALURU = "Bengaluru, Karnataka, India"
    MUMBAI = "Mumbai, Maharashtra, India"
    DELHI = "New Delhi, India"
    GURUGRAM = "Gurugram, Haryana, India"
    NOIDA = "Noida, Uttar Pradesh, India"
    HYDERABAD = "Hyderabad, Telangana, India"
    PUNE = "Pune, Maharashtra, India"
    CHENNAI = "Chennai, Tamil Nadu, India"
    KOLKATA = "Kolkata, West Bengal, India"
    AHMEDABAD = "Ahmedabad, Gujarat, India"
    JAIPUR = "Jaipur, Rajasthan, India"
    REMOTE_INDIA = "Remote - India"
    REMOTE = "Remote"


class CompanyResolution(BaseModel):
    """The employer behind the vacancy (spec §1)."""

    company: str = "Unclear"
    company_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    company_evidence: CompanyEvidence = CompanyEvidence.UNCLEAR
    company_evidence_snippet: str = ""


class ExactRoleResolution(BaseModel):
    """The ACTUAL advertised role title (spec §2)."""

    exact_role: str = "Unclear"
    major_category: str = "Unclear"   # Founder's Office | Chief of Staff | Data Analyst | Data Scientist | ...
    role_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    role_evidence_snippet: str = ""


class LocationResolution(BaseModel):
    """Normalised job location (spec §3)."""

    location: str = "Not Specified"
    location_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    location_evidence_snippet: str = ""
    is_remote: bool = False
    remote_is_india: bool = False


class EmploymentResolution(BaseModel):
    """Employment type (spec §4)."""

    employment_type: str = "Unclear"
    employment_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    employment_evidence_snippet: str = ""


class ExperienceResolution(BaseModel):
    """Experience requirement for THIS role only (spec §5)."""

    experience_required: str = "Not Specified"
    experience_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    experience_evidence_snippet: str = ""


class CTCResolution(BaseModel):
    """Compensation associated with the vacancy (spec §6)."""

    ctc: str = "Not Disclosed"
    ctc_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    ctc_evidence_snippet: str = ""


class HiringManagerResolution(BaseModel):
    """Most relevant contact about the vacancy (spec §7/§8)."""

    name: str = "Unclear"
    linkedin_url: str = "Not Available"
    is_author: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: HiringManagerEvidence = HiringManagerEvidence.UNCLEAR
    evidence_snippet: str = ""
    evidence_url: str = ""       # exact public URL backing the attribution


class EmailResolution(BaseModel):
    """Verified public business/contact email (spec §9)."""

    email: str = "Not Available"
    status: EmailStatus = EmailStatus.NOT_FOUND
    source: str = ""
    evidence_url: str = ""       # exact public URL where the email was found
    source_tier: int = 0         # 1..5 SOURCE PRIORITY (0 = no provider evidence)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class EnrichedLead(BaseModel):
    """The fully enriched, verified lead.

    Deliberately carries BOTH the original (verified) lead fields and the new
    enrichment fields so it can serve as the future expanded sheet record while
    remaining side-by-side with the existing ClassifiedPost during transition.
    """

    # ── Identity ────────────────────────────────────────────────────────────
    source_link: str = ""                                  # spec §10: original LinkedIn URL, never replaced
    post_date: str = ""
    post_description: str = ""                             # spec §11: ORIGINAL post text, never the LLM summary
    llm_summary: str = ""                                  # spec §11: a SEPARATE summary field
    # ── Phase 16: application + standardized points (verbatim post literals) ──
    key_points: str = ""               # max 4 extractive lines, never invented
    apply_google_form: str = ""        # exact Google Form URL ("" when none)
    apply_link: str = ""               # best application URL ("" when none)

    # ── Company (spec §1) ───────────────────────────────────────────────────
    company_name: str = "Unclear"
    company_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    company_evidence: str = CompanyEvidence.UNCLEAR.value  # "post_text" | "job_metadata" | ...
    company_evidence_snippet: str = ""
    company_domain: str = "Not Available"                  # Phase 8: verified official domain
    company_evidence_url: str = ""                         # Phase 8: official-site URL backing it

    # ── Role (spec §2) ──────────────────────────────────────────────────────
    major_category: str = "Unclear"                        # Founder's Office | Chief of Staff | Data Analyst | Data Scientist
    exact_role: str = "Unclear"                            # the ACTUAL advertised title
    role_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    role_evidence_snippet: str = ""
    india_relevance: str = "Unclear"                       # spec objective #8

    # ── Location / employment / experience / CTC ────────────────────────────
    location: str = "Not Specified"                        # spec §3
    location_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_remote: bool = False
    remote_is_india: bool = False
    employment_type: str = "Unclear"                       # spec §4
    employment_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    experience_required: str = "Not Specified"             # spec §5
    experience_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    ctc: str = "Not Disclosed"                             # spec §6
    ctc_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # ── Contact (spec §7/§8/§9) ─────────────────────────────────────────────
    hiring_manager_name: str = "Unclear"
    hiring_manager_linkedin: str = "Not Available"
    hiring_manager_is_author: bool = False
    hiring_manager_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    hiring_manager_evidence: str = HiringManagerEvidence.UNCLEAR.value
    hiring_manager_evidence_snippet: str = ""
    hiring_manager_evidence_url: str = ""                  # Phase 8: public URL backing the contact

    cold_email: str = "Not Available"                      # spec §9
    email_status: str = EmailStatus.NOT_FOUND.value
    email_source: str = ""
    email_evidence_url: str = ""                           # Phase 8: exact public URL where found
    email_source_tier: int = 0                             # Phase 8: SOURCE PRIORITY 1..5
    email_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    contact_discovery_status: str = "NOT_FOUND"            # Phase 8: provider status for this lead

    # ── Evidence / summary ──────────────────────────────────────────────────
    evidence_snippets: List[str] = Field(default_factory=list)  # spec §12 compact evidence

    # ── Confidence & status ─────────────────────────────────────────────────
    verification_confidence: float = Field(default=0.0, ge=0.0, le=1.0)  # spec §13 (unchanged from verifier)
    enrichment_confidence: float = Field(default=0.0, ge=0.0, le=1.0)    # spec §13 (NEW, separate)
    data_quality_score: float = Field(default=0.0, ge=0.0, le=1.0)       # Phase 8: transparent quality score
    verification_status: str = "New"                       # from the verifier (New/Review)
    enrichment_status: str = EnrichmentStatus.REVIEW.value # spec §14

    # ── Provenance (never fabricated) ───────────────────────────────────────
    enrichment_notes: List[str] = Field(default_factory=list)
    llm_used: bool = False
