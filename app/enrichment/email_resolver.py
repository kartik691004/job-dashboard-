"""
email_resolver.py — verified public contact email discovery (spec §9).

THIS IS THE NO-FABRICATION BOUNDARY. The "Cold Email" field is defined as:

    "Verified public contact email for the most relevant person/company
    associated with the vacancy."

An email is returned ONLY when it actually appears in legitimate public
evidence. The resolver NEVER:
  - guesses firstname@company.com
  - guesses first.last@company.com
  - generates emails from domain patterns
  - invents recruiter emails
  - uses private/personal data
  - fabricates an email because the company domain is known

Sources considered (high-confidence first):
  1. A literal email address in the LinkedIn post text (explicit, public).
  2. A verified public email handed in from an external/company source that the
     caller inspected (via email_source map) — never synthesized.

"Not Available" is a SUCCESSFUL safe outcome. A fabricated email is a FAILURE.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from app.enrichment.schemas import EmailResolution, EmailStatus

_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

# Domains that are almost never a legitimate application email.
_BLOCKLIST_DOMAINS = (
    "example.com", "email.com", "mailinator.com", "yopmail.com",
    "gmail.com",   # a gmail address is personal, never a public business contact
)

_PUBLIC_EMAIL_PREFIXES = (
    "careers", "jobs", "hiring", "hr", "talent", "apply",
    "contact", "hello", "info", "recruit", "work",
)


def _provider_email_for(provider_evidence, addr: str):
    """Find the exact VerifiedEmail provenance for `addr` in a provider's
    ContactEvidence (evidence URL + source tier). None when unavailable —
    never fabricates."""
    if provider_evidence is None:
        return None
    try:
        for ve in provider_evidence.verified_emails:
            if ve.email and ve.email.strip().lower() == (addr or "").lower():
                return ve
    except Exception:
        return None
    return None


def _is_public_business_email(addr: str) -> bool:
    """Heuristic guard: only obvious, explicitly-public business emails pass.

    This is deliberately conservative. If a role/company shows only a personal
    Gmail or an ambiguous address, we treat it as NOT_FOUND rather than risk a
    false / private contact.
    """
    if not addr or "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    domain = domain.lower()
    if any(domain.endswith(d) for d in _BLOCKLIST_DOMAINS):
        return False
    # A role-based public mailbox is the strongest signal of a verified public
    # contact (careers@company.com, hello@company.com, ...).
    if local.strip().lower() in _PUBLIC_EMAIL_PREFIXES:
        return True
    return False


def from_post_text(post_text: str) -> Optional[str]:
    """Return a verified public business email literally present in the post."""
    if not post_text:
        return None
    for m in _EMAIL_PATTERN.finditer(post_text):
        addr = m.group(0)
        if _is_public_business_email(addr):
            return addr
    return None


def resolve(
    post_text: str = "",
    post_body_has_application_method: str = "",
    company_domain: str = "",
    verified_emails: Optional[Dict[str, str]] = None,
    provider_evidence=None,
    post_evidence_url: str = "",
) -> EmailResolution:
    """Resolve a verified public contact email, or report it missing.

    :param verified_emails: map of {source: "email@domain"} where the CALLER has
        already verified the email from a legitimate public source (company
        contact page, careers page, reliable public directory). Only these are
        accepted — no synthesis, no pattern guessing. Empty/missing => none.
    :param provider_evidence: optional ContactEvidence from a Phase-8
        contact-discovery provider, used to attach the exact evidence URL and
        SOURCE PRIORITY tier of the chosen verified email (never to fabricate).
    :param post_evidence_url: the LinkedIn post URL — evidence for an email
        literally present in the post text (TIER 3), when provided.
    """
    # 1. Literal public business email in the post text (the strongest signal).
    literal = from_post_text(post_text)
    source = "post_text" if (post_text and literal) else ""

    # 2. Externally verified emails supplied by the caller.
    # IMPORTANT: even "verified" external emails must pass the public-business
    # guard. A caller (or a contact-discovery provider) can hand in a personal
    # Gmail or an obviously private address; no-fabrication means we never
    # promote a non-public mailbox to a business contact, no matter who handed
    # it in or how confident they were.
    external = ""
    ext_source = ""
    if verified_emails is not None:
        for src, addr in verified_emails.items():
            if addr and "@" in addr and "." in addr.split("@")[-1] \
                    and _is_public_business_email(addr):
                external, ext_source = addr, src
                break

    # Post-text email wins (it is the most explicit, on-record attribution).
    if literal:
        chosen, chosen_source = literal, source
    else:
        chosen, chosen_source = external, ext_source

    if chosen:
        evidence_url, source_tier = "", 0
        if chosen_source == "post_text":
            evidence_url, source_tier = post_evidence_url or "", 3  # LinkedIn post source
        else:
            ve = _provider_email_for(provider_evidence, chosen)
            if ve is not None:
                evidence_url, source_tier = ve.evidence_url, ve.source_tier
        return EmailResolution(
            email=chosen,
            status=EmailStatus.FOUND,
            source=chosen_source,
            evidence_url=evidence_url,
            source_tier=source_tier,
            confidence=0.98 if chosen_source == "post_text" else 0.9,
        )

    # A company/role was found but no verified public email => NOT_PUBLIC.
    if company_domain:
        return EmailResolution(
            email="Not Available",
            status=EmailStatus.NOT_PUBLIC,
            source="",
            confidence=0.0,
        )

    return EmailResolution(
        email="Not Available",
        status=EmailStatus.NOT_FOUND,
        source="",
        confidence=0.0,
    )
