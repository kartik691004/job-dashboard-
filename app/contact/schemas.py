"""
contact/schemas.py — data contract for verified contact discovery (Phase 7).

CONTACT DISCOVERY (spec §24) is the process of finding a REAL, PUBLIC,
verifiable outreach contact for an ACCEPTED lead — a company's official contact
email, careers/recruiting mailbox, or a publicly listed hiring contact — WITHOUT
fabrication, WITHOUT scraping private/personal data, and WITHOUT bypassing login
walls.

NO-FABRICATION BOUNDARY:
  - Every field defaults to an honest sentinel ("Not Available", "Not Found").
  - A provider MAY return only information it actually obtained from a public,
    verifiable source. It must supply evidence (url + snippet) for every claim.
  - A provider MUST NOT synthesize emails from domain patterns, invent LinkedIn
    profiles, or guess recruiter contacts.

The lead enrichment layer consumes this contract and feeds verified emails into
the existing app.enrichment.email_resolver (which already enforces
no-fabrication), so even a provider that returns garbage cannot fabricate.
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ContactStatus(str, Enum):
    """Whether contact discovery produced verified, public, usable data."""

    FOUND = "FOUND"                 # verified public contact(s) found
    PARTIAL = "PARTIAL"             # company/domain found but no usable email/contact
    NOT_FOUND = "NOT_FOUND"         # nothing usable found
    ERROR = "ERROR"                 # provider failed; caller must treat as NOT_FOUND


# SOURCE PRIORITY (spec: "Create a clear source hierarchy"). TIER 1 is the
# strongest evidence; TIER 5 the weakest. Lower-quality evidence must NEVER
# silently override stronger evidence — extractors assign tiers and consumers
# (email priority, contact confidence) honour them.
#   TIER 1 — official company careers / job / contact page
#   TIER 2 — official company website (root, about, team)
#   TIER 3 — LinkedIn company / job source
#   TIER 4 — verified public professional source
#   TIER 5 — search result snippet only
SOURCE_TIER_OFFICIAL_PAGE = 1
SOURCE_TIER_OFFICIAL_SITE = 2
SOURCE_TIER_LINKEDIN = 3
SOURCE_TIER_PROFESSIONAL = 4
SOURCE_TIER_SEARCH_SNIPPET = 5


class VerifiedEmail(BaseModel):
    """One verified public business email, with provenance."""

    email: str
    source: str = ""                 # e.g. "company_careers_page", "company_contact_page"
    evidence_url: str = ""           # exact public URL where it was found
    evidence_snippet: str = ""       # short quote showing it in context
    source_tier: int = SOURCE_TIER_SEARCH_SNIPPET  # 1..5, see SOURCE_TIER_* constants
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    verified: bool = False           # True = caller/company-side confirmed public


class VerifiedContact(BaseModel):
    """A publicly listed person responsible for hiring (evidence-backed)."""

    name: str = "Unclear"
    role: str = ""
    linkedin_url: str = "Not Available"   # ONLY a real linkedin.com/in/... URL
    evidence_url: str = ""
    evidence_snippet: str = ""
    source_tier: int = SOURCE_TIER_SEARCH_SNIPPET  # 1..5, see SOURCE_TIER_* constants
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ContactEvidence(BaseModel):
    """The result of a contact-discovery attempt for one lead/company."""

    status: ContactStatus = ContactStatus.NOT_FOUND
    company: str = "Unclear"
    company_domain: str = "Not Available"     # official domain, evidence-backed only
    company_domain_source: str = "none"       # where the domain came from: "none" | "curated" | "search" | ...
    company_domain_evidence_url: str = ""     # public URL backing the domain claim
    verified_emails: List[VerifiedEmail] = Field(default_factory=list)
    contacts: List[VerifiedContact] = Field(default_factory=list)
    evidence_urls: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: List[str] = Field(default_factory=list)

    def best_email(self) -> Optional[VerifiedEmail]:
        """Highest-confidence verified public email, or None."""
        if not self.verified_emails:
            return None
        return max(self.verified_emails, key=lambda e: e.confidence)
