"""
app.contact — VERIFIED CONTACT DISCOVERY (Phase 7 seam → Phase 8 real provider).

A pluggable seam for resolving an ACCEPTED lead's company into verified public
outreach data (official domain, public careers/contact emails, publicly listed
hiring contacts). The default provider is offline and fail-closed; the real
web-search provider (WebSearchContactDiscoveryProvider) is opt-in and fully
offline-testable through injected search/fetch/cache dependencies.

See:
  - schemas.ContactEvidence  : the data contract
  - provider.ContactDiscoveryProvider  : the Protocol any search provider must satisfy
  - provider.NullContactDiscoveryProvider : the safe production default
  - web_search_provider.WebSearchContactDiscoveryProvider : the REAL provider (Phase 8)
  - cache.ContactDiscoveryCache : TTL'd reuse of resolved companies
"""
from app.contact.cache import ContactDiscoveryCache
from app.contact.provider import (
    ContactDiscoveryError,
    ContactDiscoveryProvider,
    NullContactDiscoveryProvider,
)
from app.contact.schemas import (
    ContactEvidence,
    ContactStatus,
    VerifiedContact,
    VerifiedEmail,
)
from app.contact.web_search_provider import (
    WebSearchContactDiscoveryProvider,
    get_contact_provider,
)

__all__ = [
    "ContactDiscoveryError",
    "ContactDiscoveryProvider",
    "ContactEvidence",
    "ContactStatus",
    "ContactDiscoveryCache",
    "NullContactDiscoveryProvider",
    "VerifiedContact",
    "VerifiedEmail",
    "WebSearchContactDiscoveryProvider",
    "get_contact_provider",
]
