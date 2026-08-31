"""
test_contact_discovery.py — Phase 7 VERIFIED CONTACT DISCOVERY tests.

Covers the contact-discovery provider seam (app.contact) and how the enricher
consumes it: verified public emails / contacts flow in ONLY when evidence-backed,
and a missing or failing provider never fabricates a contact or email.

All offline: no web search, no Apify, no Google Sheets, no DRY_RUN changes.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.contact import (
    ContactDiscoveryError,
    ContactDiscoveryProvider,
    ContactEvidence,
    ContactStatus,
    NullContactDiscoveryProvider,
    VerifiedContact,
    VerifiedEmail,
)
from app.enrichment import Enricher
from app.enrichment.schemas import EmailStatus


def make_evidence(*, status=ContactStatus.FOUND, emails=(), contacts=(),
                  company="Acme", domain="acme.com"):
    return ContactEvidence(
        status=status,
        company=company,
        company_domain=domain,
        verified_emails=list(emails),
        contacts=list(contacts),
        evidence_urls=["https://acme.com/careers"],
        confidence=0.9 if status == ContactStatus.FOUND else 0.0,
    )


class FakeFoundProvider:
    """A provider that legitimately returns a verified public email + contact."""

    def discover(self, company, **kw):
        return make_evidence(
            emails=[VerifiedEmail(email="careers@acme.com",
                                  source="company_careers_page", verified=True,
                                  evidence_url="https://acme.com/careers",
                                  confidence=0.95)],
            contacts=[VerifiedContact(name="Priya Sharma", role="Talent Lead",
                                      linkedin_url="https://linkedin.com/in/priya-sharma",
                                      evidence_url="https://acme.com/team",
                                      confidence=0.8)],
        )


class EmptyProvider:
    """A provider that finds nothing — must not fabricate."""

    def discover(self, company, **kw):
        return make_evidence(status=ContactStatus.NOT_FOUND, emails=[], contacts=[])


class FailingProvider:
    """A provider that errors — must FAIL CLOSED (never fabricate)."""

    def discover(self, company, **kw):
        raise ContactDiscoveryError("network down")


class HijackProvider:
    """A hostile provider that hands in an unverified email and a fake URL.
    The no-fabrication rule must keep them out."""

    def discover(self, company, **kw):
        return make_evidence(
            emails=[VerifiedEmail(email="ravi.kumar@gmail.com",
                                  source="scraped_personal", verified=False,
                                  confidence=0.9)],
            contacts=[VerifiedContact(name="Hacker", role="",
                                      linkedin_url="https://linkedin.com/in/hacker",
                                      confidence=0.99)],
        )


# ── 1. Provider seam: Protocol exists and is exportable ──────────────────────

def test_provider_protocol_is_runtime_checkable():
    assert isinstance(FakeFoundProvider(), ContactDiscoveryProvider)
    assert isinstance(NullContactDiscoveryProvider(), ContactDiscoveryProvider)


def test_invalid_provider_is_not_a_provider():
    # Structural typing: an object with the right method passes the Protocol;
    # an unrelated object does not (isinstance with runtime_checkable Protocol
    # checks structure, never raising TypeError).
    class NotAProvider:
        pass

    assert not isinstance(NotAProvider(), ContactDiscoveryProvider)
    assert isinstance(FakeFoundProvider(), ContactDiscoveryProvider)


# ── 2. Null provider: fail-closed default, never network, never invents ──────

def test_null_provider_returns_not_found_without_network():
    p = NullContactDiscoveryProvider()
    ev = p.discover("Acme")
    assert ev.status == ContactStatus.NOT_FOUND
    assert ev.verified_emails == []
    assert ev.company_domain == "Not Available"
    assert ev.best_email() is None


def test_null_provider_is_the_enricher_default():
    # Enricher() with no contact_provider must perform NO discovery and NOT
    # fabricate a contact or email.
    e = Enricher()
    res = e.enrich("We are hiring a Founder's Office Associate at Acme in Mumbai.",
                   job_metadata_company="Acme")
    assert res.cold_email in ("Not Available", "")
    assert res.hiring_manager_name in ("Unclear", "")


# ── 3. Verified email flows through a real provider ──────────────────────────

def test_found_provider_feeds_verified_email():
    e = Enricher(contact_provider=FakeFoundProvider())
    res = e.enrich("We are hiring a Founder's Office Associate at Acme in Mumbai.",
                   job_metadata_company="Acme", source_link="https://lnkd.in/x")
    assert res.cold_email == "careers@acme.com"
    assert res.email_status == EmailStatus.FOUND.value
    assert res.email_source == "company_careers_page"


def test_found_provider_feeds_public_contact_hint():
    e = Enricher(contact_provider=FakeFoundProvider())
    res = e.enrich("We are hiring a Founder's Office Associate at Acme in Mumbai.",
                   job_metadata_company="Acme")
    assert res.hiring_manager_name == "Priya Sharma"
    assert res.hiring_manager_linkedin == "https://linkedin.com/in/priya-sharma"


# ── 4. No fabrication: empty / failing / hostile providers ──────────────────

def test_empty_provider_causes_no_fabrication():
    e = Enricher(contact_provider=EmptyProvider())
    res = e.enrich("We are hiring a Founder's Office Associate at Acme in Mumbai.",
                   job_metadata_company="Acme")
    assert res.cold_email in ("Not Available", "")
    assert res.hiring_manager_name in ("Unclear", "")


def test_failing_provider_fails_closed_without_fabrication():
    e = Enricher(contact_provider=FailingProvider())
    res = e.enrich("We are hiring a Founder's Office Associate at Acme in Mumbai.",
                   job_metadata_company="Acme")
    assert res.cold_email in ("Not Available", "")
    assert res.hiring_manager_name in ("Unclear", "")


def test_hostile_provider_cannot_smuggle_personal_or_fake_contact():
    # A provider that "finds" a personal Gmail and a guessed LinkedIn slug must
    # not get them into the lead: the email resolver guards personal domains
    # and the enricher only trusts verified evidence.
    e = Enricher(contact_provider=HijackProvider())
    res = e.enrich("We are hiring a Founder's Office Associate at Acme in Mumbai.",
                   job_metadata_company="Acme")
    assert res.cold_email in ("Not Available", "")   # gmail never a business contact
    assert res.hiring_manager_name in ("Unclear", "")   # unverified contact dropped


def test_verified_gmail_supplied_directly_is_still_not_forced():
    # Even an explicitly-handed-in personal Gmail must not become the cold email.
    e = Enricher()
    res = e.enrich("We are hiring a Founder's Office Associate at Acme in Mumbai.",
                   job_metadata_company="Acme",
                   verified_emails={"recruiter_personal": "priya.sharma@gmail.com"})
    assert res.cold_email in ("Not Available", "")
    assert res.email_status != EmailStatus.FOUND.value


# ── 5. Company hint is only used when company is actually resolved ───────────

def test_no_contact_probe_when_company_unclear():
    # With no resolvable company the enricher must not even call the provider
    # with a garbage company name (fail-closed at the seam).
    calls = {"n": 0}

    class CountingProvider:
        def discover(self, company, **kw):
            calls["n"] += 1
            return make_evidence()

    e = Enricher(contact_provider=CountingProvider())
    res = e.enrich("We are hiring a Chief of Staff in Pune. Apply now.")  # no company
    assert res.company_name in ("Unclear", "")
    # Enricher may probe only with a real company; no company => no probe.
    # (The discover() call is guarded by company != Unclear inside enrich.)
    assert calls["n"] == 0


# ── 6. In-post / LLM evidence outranks a provider hint ───────────────────────

def test_explicit_llm_manager_outranks_provider_hint():
    e = Enricher(contact_provider=FakeFoundProvider())
    res = e.enrich(
        "I'm hiring a Founder's Office Associate at Acme in Mumbai.",
        job_metadata_company="Acme",
        author_name="Ravi Kumar",
        llm_hiring_manager_name="Ravi Kumar",
    )
    # The author (explicit LLM attribution, first-person hiring) wins.
    assert res.hiring_manager_name == "Ravi Kumar"
    assert res.hiring_manager_is_author is True


# ── 7. No LinkedIn URL fabricated from a name ────────────────────────────────

def test_provider_contact_linkedin_never_fabricated_without_evidence():
    # A provider that returns a person but NO evidence URL must not be treated
    # as a verifiable contact: unattributed contacts are dropped (no fabrication).
    class NameOnlyProvider:
        def discover(self, company, **kw):
            return make_evidence(
                contacts=[VerifiedContact(name="Priya Sharma", role="Talent",
                                          linkedin_url="Not Available",
                                          confidence=0.6)],
            )

    e = Enricher(contact_provider=NameOnlyProvider())
    res = e.enrich("We are hiring a Founder's Office Associate at Acme in Mumbai.",
                   job_metadata_company="Acme")
    assert res.hiring_manager_name in ("Unclear", "")
    assert res.hiring_manager_linkedin in ("Not Available", "")