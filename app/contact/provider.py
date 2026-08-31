"""
contact/provider.py — the contact-discovery provider boundary (Phase 7).

A ContactDiscoveryProvider turns an ACCEPTED lead's resolved company into
verified public contact evidence (official domain, public careers/contact
emails, publicly listed hiring contacts). It is a pluggable seam: the pipeline
depends on this Protocol, never on a concrete search engine, so a web-search or
company-directory provider can be added later WITHOUT changing the verifier,
enricher, orchestrator, or Sheets writer.

SECURITY / PRIVACY / NO-FABRICATION requirements for ANY implementation:
  - Only PUBLIC, verifiable sources. Never private/personal data.
  - Never bypass login walls. Never use unauthorized/private LinkedIn data.
  - Never synthesize emails from domain patterns. Never invent profiles.
  - Supply evidence (url + snippet) for every claim.
  - Fail CLOSED: raise ContactDiscoveryError on any failure so the caller
    degrades to NOT_FOUND instead of trusting a partially-invented payload.

The default provider (NullContactDiscoveryProvider) returns NOT_FOUND for every
call and never touches the network. That is the safe, production default: the
pipeline is fully offline and no-fabrication even before a search provider is
added.
"""
from typing import Dict, Protocol, runtime_checkable

from app.contact.schemas import ContactEvidence, ContactStatus


class ContactDiscoveryError(RuntimeError):
    """Raised when a provider fails. Caller treats this as NOT_FOUND (fail closed)."""


@runtime_checkable
class ContactDiscoveryProvider(Protocol):
    """Contract: resolve a resolved company into verified public contact data.

    Implementations MUST:
      - accept a company name (and optional post metadata) and return a
        ContactEvidence exactly once; never raise for a simple "not found".
      - raise ContactDiscoveryError for network/API/parse failures (fail closed).
      - never fabricate emails, URLs, or contacts.
    """

    def discover(self, company: str, *, role: str = "",
                 source_text: str = "", extras: Dict | None = None) -> ContactEvidence:
        """Resolve contact evidence for `company`. Never synthesize."""
        ...


class NullContactDiscoveryProvider:
    """Offline, fail-closed default. Never queries a network; never invents.

    This is what Orchestrator uses by default, so the live pipeline performs no
    external contact lookups unless a real provider is explicitly supplied.
    """

    def discover(self, company: str, *, role: str = "",
                 source_text: str = "", extras: Dict | None = None) -> ContactEvidence:
        return ContactEvidence(
            status=ContactStatus.NOT_FOUND,
            company=company or "Unclear",
            company_domain="Not Available",
            verified_emails=[],
            contacts=[],
            evidence_urls=[],
            confidence=0.0,
            notes=["NullContactDiscoveryProvider: no provider configured; "
                   "no contact discovery performed (fail closed)."],
        )
