"""
test_contact_web_provider.py — Phase 8 WEB-SEARCH CONTACT DISCOVERY tests.

Covers the real pipeline the Phase-8 provider introduces: domain resolution,
tiered public-page fetch, literal-only email extraction (never guessed EMAIL
STANDARD), explicit-signal-only hiring-manager extraction, caching reuse,
request budget/throttle, fail-CLOSED error handling, no-fabrication enforcement
(end-to-end through the Enricher), and the get_contact_provider factory.

ALL OFFLINE: every scenario injects FakeSearch / FakeFetch / in-memory cache —
never a real search engine, never httpx, never Sheets, never DRY_RUN changes.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.contact import (
    ContactDiscoveryCache,
    ContactDiscoveryError,
    ContactStatus,
    VerifiedContact,
    VerifiedEmail,
    WebSearchContactDiscoveryProvider,
    get_contact_provider,
)
from app.contact.web import SearchError, SearchResult
from app.enrichment import Enricher
from app.enrichment.schemas import EmailStatus


# ── fakes ─────────────────────────────────────────────────────────────────────

class FakeSearch:
    """Deterministic in-memory search backend. url -> list of SearchResult."""

    def __init__(self, results=None, raise_on=None):
        self.results = results or {}   # query -> [SearchResult]
        self.queries = []
        self.raise_on = raise_on       # query substring that should raise

    def search(self, query, max_results=5):
        self.queries.append(query)
        if self.raise_on and self.raise_on in query.lower():
            raise SearchError("backend down")
        return (self.results.get(query) or self.results.get(query.lower())
                or [])[:max_results]


class FakeFetch:
    """Deterministic in-memory page fetcher. url -> html body."""

    def __init__(self, pages=None, raise_on=None, ok=None):
        self.pages = pages or {}   # url -> html
        self.calls = []
        self.raise_on = raise_on or []  # url substrings that raise
        self.ok = ok               # optional set of urls allowed

    def get(self, url, timeout_s=12.0):
        self.calls.append(url)
        for bad in self.raise_on:
            if bad in url:
                raise ContactDiscoveryError("fetch failed")
        return self.pages.get(url, "")


def make_search(urls):
    return FakeSearch({
        '"Acme" official website': [SearchResult(title="Acme", url=urls[0])],
        '"Zap Labs" official website': [SearchResult(title="Zap", url=urls[0])],
        '"NoDomain Co" official website': [SearchResult(title="news", url=urls[0])],
    })


def make_provider(pages=None, results=None, cache=None, **kw):
    search = results if isinstance(results, FakeSearch) else make_search(list((pages or {}).keys()))
    return WebSearchContactDiscoveryProvider(
        search_provider=search,
        fetcher=FakeFetch(pages=pages or {}),
        cache=cache or ContactDiscoveryCache(),
        max_pages=kw.get("max_pages", 4),
        max_requests=kw.get("max_requests", 8),
        min_interval_s=0.0,
    )


def careers_html(*, email="careers@acme.com", mgr_html=""):
    return (
        "<html><body>"
        "<h1>Careers at Acme</h1>"
        f'<a href="mailto:{email}">Email us</a>'
        f"{mgr_html}"
        "</body></html>"
    )


MANAGER_HTML = (
    "Our Talent Lead is <a href=\"https://www.linkedin.com/in/priya-sharma\">"
    "Priya Sharma</a>. Reach out to Priya Sharma for roles."
)


# ── 1. Provider Protocol / factory (fail-closed default) ─────────────────────

def test_factory_default_is_null_offline():
    p = get_contact_provider("null")
    ev = p.discover("Acme")
    assert ev.status == ContactStatus.NOT_FOUND
    assert ev.verified_emails == []
    assert ev.company_domain == "Not Available"


def test_factory_unknown_name_falls_back_to_null():
    p = get_contact_provider("totally_bogus")
    ev = p.discover("Acme")
    assert ev.status == ContactStatus.NOT_FOUND


def test_factory_web_search_builds_real_provider():
    p = get_contact_provider("web_search")
    assert isinstance(p, WebSearchContactDiscoveryProvider)


# ── 2. Domain resolution ─────────────────────────────────────────────────────

def test_curated_domain_hint_is_used_without_search():
    # razorpays-known domain is in the curated hints -> no search call at all.
    rec = []
    class CntSearch:
        def search(self, q, n=5):
            rec.append(q)
            return []
    p = WebSearchContactDiscoveryProvider(search_provider=CntSearch(),
                                          fetcher=FakeFetch({}), min_interval_s=0.0)
    ev = p.discover("Razorpay")
    assert ev.company_domain == "razorpay.com"
    assert ev.company_domain_source == "curated"
    assert rec == []          # no network search for a curated domain


def test_search_resolves_official_domain_with_evidence():
    pages = {"https://acme.com": "<html>Acme homepage</html>"}
    p = make_provider(pages=pages)
    ev = p.discover("Acme")
    assert ev.company_domain == "acme.com"
    assert ev.company_domain_source == "search"
    assert ev.company_domain_evidence_url == "https://acme.com"


def test_platform_host_is_never_treated_as_official_domain():
    # LinkedIn / social results must not become the "official company domain".
    search = FakeSearch({
        '"Acme" official website':
            [SearchResult(title="Acme", url="https://www.linkedin.com/company/acme")],
    })
    p = make_provider(results=search)
    ev = p.discover("Acme")
    assert ev.status == ContactStatus.NOT_FOUND
    assert ev.company_domain == "Not Available"


def test_no_domain_found_yields_not_found_without_emails():
    search = FakeSearch({
        '"Acme" official website':
            [SearchResult(title="odd news", url="https://somethingelse.com/x")],
    })
    p = make_provider(results=search)
    ev = p.discover("Acme")
    assert ev.status == ContactStatus.NOT_FOUND
    assert ev.verified_emails == []
    assert ev.contacts == []


# ── 3. Literal email extraction + EMAIL STANDARD ordering ────────────────────

def test_email_from_careers_page_is_found_with_evidence():
    pages = {"https://acme.com/careers": careers_html(email="careers@acme.com")}
    p = make_provider(pages=pages)
    ev = p.discover("Acme")
    assert ev.status == ContactStatus.FOUND
    assert len(ev.verified_emails) == 1
    ve = ev.verified_emails[0]
    assert ve.email == "careers@acme.com"
    assert ve.evidence_url == "https://acme.com/careers"
    assert ve.verified is True


def test_email_standard_orders_careers_before_contact():
    pages = {"https://acme.com/careers": careers_html(
                 email="careers@acme.com") + '<a href="mailto:info@acme.com">i</a>'}
    p = make_provider(pages=pages)
    ev = p.discover("Acme")
    emails = [v.email for v in ev.verified_emails]
    assert emails[0] == "careers@acme.com"   # EMAIL STANDARD: careers > info
    assert "info@acme.com" in emails


def test_personal_gmail_is_never_a_business_contact():
    pages = {"https://acme.com/careers": careers_html(
                 email="priya.kumar@gmail.com")}
    p = make_provider(pages=pages)
    ev = p.discover("Acme")
    assert ev.verified_emails == []          # no public business contact
    assert ev.status in (ContactStatus.PARTIAL, ContactStatus.NOT_FOUND)


def test_named_employee_email_passes_only_with_matching_official_domain():
    # rohit.gupta@acme.com on the official acme.com site -> literal + domain match.
    pages = {"https://acme.com": "<body>Rohit Gupta at rohit.gupta@acme.com</body>"}
    p = make_provider(pages=pages)
    ev = p.discover("Acme")
    assert any(v.email == "rohit.gupta@acme.com" for v in ev.verified_emails)


# ── 4. Hiring manager extraction (explicit signals + real LinkedIn URLs) ─────

def test_manager_extracted_from_reach_out_with_linkedin_url():
    pages = {"https://acme.com/team": f"<html><body>{MANAGER_HTML}</body></html>"}
    p = make_provider(pages=pages)
    ev = p.discover("Acme")
    names = [c.name for c in ev.contacts]
    assert any("Priya Sharma" in n for n in names)
    mgr = next(c for c in ev.contacts if "Priya" in c.name)
    assert mgr.linkedin_url == "https://www.linkedin.com/in/priya-sharma"
    assert mgr.evidence_url == "https://acme.com/team"


def test_manager_linkedin_url_is_never_invented_from_name():
    # A name WITHOUT any linkedin.com/in/ URL must never get a fabricated URL.
    pages = {"https://acme.com/team":
             "<html><body>Reach out to Priya Sharma for roles at Acme.</body></html>"}
    p = make_provider(pages=pages)
    ev = p.discover("Acme")
    for c in ev.contacts:
        if "Priya Sharma" in c.name:
            assert c.linkedin_url in ("Not Available", "")
            assert "linkedin.com" not in c.linkedin_url


def test_bare_name_without_signal_is_not_a_contact():
    pages = {"https://acme.com/team": "<html><body>Priya Sharma loves Acme.</body></html>"}
    p = make_provider(pages=pages)
    ev = p.discover("Acme")
    assert ev.contacts == []   # no reach-out / no LinkedIn / no talent role


# ── 5. Dedupe / evidence HTML ────────────────────────────────────────────────

def test_duplicate_email_is_deduped():
    pages = {"https://acme.com/careers": careers_html(
                 email="careers@acme.com") + '<a href="mailto:careers@acme.com">x</a>'}
    p = make_provider(pages=pages)
    ev = p.discover("Acme")
    assert len(ev.verified_emails) == 1


def test_conflicting_sources_keep_highest_tier():
    # Same careers@acme.com found on the careers page (TIER 1) and a weaker
    # snippet — dedupe must keep the TIER 1 provenance (higher source tier).
    pages = {"https://acme.com/careers": careers_html(email="careers@acme.com")}
    p = make_provider(pages=pages)
    ev = p.discover("Acme")
    ve = ev.verified_emails[0]
    assert ve.source_tier == 1
    assert ve.evidence_url == "https://acme.com/careers"


# ── 6. Caching ───────────────────────────────────────────────────────────────

def test_second_discover_is_a_cache_hit_no_network():
    pages = {"https://acme.com/careers": careers_html()}
    cache = ContactDiscoveryCache()
    p = make_provider(pages=pages, cache=cache)
    ev1 = p.discover("Acme")
    fetcher = p._fetch
    n_calls = len(fetcher.calls)
    ev2 = p.discover("Acme")
    assert ev2.status == ev1.status
    assert len(fetcher.calls) == n_calls      # no second fetch -> used cache
    assert cache.hits >= 1


def test_cache_returns_same_company_result_for_normalised_name():
    pages = {"https://acme.com/careers": careers_html()}
    cache = ContactDiscoveryCache()
    p = make_provider(pages=pages, cache=cache)
    p.discover("Acme")
    ev2 = p.discover("  acme  ")              # different casing/spacing
    assert ev2.company in ("Acme", "acme")
    assert cache.hits >= 1


# ── 7. Budget / throttle / error resilience ──────────────────────────────────

def test_request_budget_halts_after_limit():
    pages = {f"https://acme.com/{i}": "<html></html>" for i in range(20)}
    p = WebSearchContactDiscoveryProvider(
        search_provider=make_search(["https://acme.com"]),
        fetcher=FakeFetch(pages=pages),
        max_pages=20, max_requests=3, min_interval_s=0.0,
    )
    ev = p.discover("Acme")
    assert p.request_count <= 3
    assert any("budget" in n for n in ev.notes)


def test_one_bad_page_does_not_kill_discovery():
    # careers page raises; the official root page still yields a public email.
    pages = {
        "https://acme.com/careers": "",
        "https://acme.com": careers_html(email="info@acme.com"),
    }
    p = WebSearchContactDiscoveryProvider(
        search_provider=make_search(["https://acme.com"]),
        fetcher=FakeFetch(pages=pages, raise_on=["/careers"]),
        max_pages=4, max_requests=8, min_interval_s=0.0,
    )
    ev = p.discover("Acme")
    assert any(v.email == "info@acme.com" for v in ev.verified_emails)


def test_search_failure_degrades_to_none_not_crash():
    search = FakeSearch(raise_on="acme")
    p = WebSearchContactDiscoveryProvider(search_provider=search,
                                          fetcher=FakeFetch({}), min_interval_s=0.0)
    ev = p.discover("Acme")
    assert ev.company_domain == "Not Available"
    assert ev.status == ContactStatus.NOT_FOUND


def test_unexpected_error_fails_closed_as_contact_error():
    class BoomSearch:
        def search(self, q, n=5):
            raise RuntimeError("unexpected")
    p = WebSearchContactDiscoveryProvider(search_provider=BoomSearch(),
                                          fetcher=FakeFetch({}), min_interval_s=0.0)
    with pytest.raises(ContactDiscoveryError):
        p.discover("Acme")


def test_unresolvable_company_is_noop_not_found():
    p = make_provider(pages={"https://acme.com": "<html></html>"})
    ev = p.discover("Not Available")   # sentinel -> no lookup at all
    assert ev.status == ContactStatus.NOT_FOUND
    assert ev.notes and any("no resolvable company" in n for n in ev.notes)


# ── 8. End-to-end no-fabrication through the Enricher ────────────────────────

def test_enricher_accepts_provider_email_with_evidence_url():
    class Prov:
        def discover(self, company, **kw):
            from app.contact.schemas import ContactEvidence
            return ContactEvidence(
                status=ContactStatus.FOUND, company=company, company_domain="acme.com",
                verified_emails=[VerifiedEmail(email="careers@acme.com", source="company_careers_page",
                                               verified=True, evidence_url="https://acme.com/careers",
                                               source_tier=1, confidence=0.95)],
            )
    e = Enricher(contact_provider=Prov())
    res = e.enrich("We are hiring a Founder's Office Associate at Acme in Mumbai.",
                   job_metadata_company="Acme", source_link="https://lnkd.in/cos")
    assert res.cold_email == "careers@acme.com"
    assert res.email_status == EmailStatus.FOUND.value
    assert res.email_evidence_url == "https://acme.com/careers"


def test_enricher_drops_provider_personal_email():
    class Prov:
        def discover(self, company, **kw):
            from app.contact.schemas import ContactEvidence
            return ContactEvidence(
                status=ContactStatus.FOUND, company=company,
                verified_emails=[VerifiedEmail(email="priya@gmail.com", source="personal",
                                               verified=False, confidence=0.9)],
            )
    e = Enricher(contact_provider=Prov())
    res = e.enrich("We are hiring a Founder's Office Associate at Acme in Mumbai.",
                   job_metadata_company="Acme")
    assert res.cold_email in ("Not Available", "")
    assert res.email_status != EmailStatus.FOUND.value


def test_enricher_surfaces_discovered_domain_and_status():
    class Prov:
        def discover(self, company, **kw):
            from app.contact.schemas import ContactEvidence
            return ContactEvidence(
                status=ContactStatus.PARTIAL, company=company,
                company_domain="acme.com",
                company_domain_source="search",
                company_domain_evidence_url="https://acme.com",
            )
    e = Enricher(contact_provider=Prov())
    res = e.enrich("We are hiring a Chief of Staff at Acme in Pune.",
                   job_metadata_company="Acme")
    assert res.company_domain == "acme.com"
    assert res.company_evidence_url == "https://acme.com"
    assert res.contact_discovery_status == ContactStatus.PARTIAL.value


def test_enricher_surfaces_data_quality_score():
    e = Enricher()
    res = e.enrich("We are hiring a Chief of Staff at Acme in Pune. Full-time.",
                   job_metadata_company="Acme",
                   classified_company="Acme", classified_exact_role="Chief of Staff",
                   classified_employment="Full-time",
                   classified_india_relevance="india")
    assert 0.0 <= res.data_quality_score <= 1.0
    assert res.data_quality_score >= 0.1


# ── 9. Source tier constants (spec: clear source hierarchy) ──────────────────

def test_source_tier_constants_ordered():
    from app.contact import schemas
    assert schemas.SOURCE_TIER_OFFICIAL_PAGE == 1
    assert schemas.SOURCE_TIER_OFFICIAL_SITE == 2
    assert schemas.SOURCE_TIER_LINKEDIN == 3
    assert schemas.SOURCE_TIER_PROFESSIONAL == 4
    assert schemas.SOURCE_TIER_SEARCH_SNIPPET == 5
    assert schemas.SOURCE_TIER_OFFICIAL_PAGE < schemas.SOURCE_TIER_OFFICIAL_SITE < \
        schemas.SOURCE_TIER_LINKEDIN < schemas.SOURCE_TIER_PROFESSIONAL < \
        schemas.SOURCE_TIER_SEARCH_SNIPPET
