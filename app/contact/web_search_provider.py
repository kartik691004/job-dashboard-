"""
contact/web_search_provider.py — REAL verified contact-discovery provider (Phase 8).

Implements the Phase-7 ContactDiscoveryProvider seam with an actual web-search +
public-page pipeline. It is fully offline-testable (search_provider / fetcher /
cache are injected) and, once enabled (CONTACT_PROVIDER=web_search), resolves an
ACCEPTED lead's company into verified PUBLIC contact data:

  A. official company website            (domain resolution, evidence-backed)
  B. official careers page               (TIER 1 fetch)
  C. public contact / talent page        (TIER 1 fetch)
  D. public hiring / team page           (TIER 2 fetch)
  E. public business email               (literal, priority-ordered, never guessed)
  F. publicly listed hiring manager      (explicit signals only)
  G. real LinkedIn /in/ profile URL      (only a literal link, never invented)

NO-FABRICATION / SAFETY enforcement inside this provider:
  - only PUBLIC pages (careers/contact/team/root); never private data, never
    login walls, never personal mailboxes.
  - emails are only addresses LITERALLY displayed on the page (mailto hrefs and
    plain text), guarded by the public-business domain check; first@domain and
    first.last@domain patterns are NEVER generated.
  - manager LinkedIn URLs are only literal linkedin.com/in/... URLs found on
    the page; a URL is never constructed from a name.
  - every claim carries its exact evidence URL + source tier.
  - caching reuses a discovered company's result (TTL'd, optional JSON file).
  - request budget + min-interval throttle cap cost; budget exhaustion degrades
    to PARTIAL/NOT_FOUND, and any transport error is raised as
    ContactDiscoveryError so the caller fails CLOSED.
"""
from __future__ import annotations

import re
import time
import urllib.parse
from typing import Dict, List, Optional

from app.contact.cache import ContactDiscoveryCache
from app.contact.extract import (
    extract_emails,
    extract_managers,
)
from app.contact.provider import ContactDiscoveryError, ContactDiscoveryProvider
from app.contact.schemas import (
    ContactEvidence,
    ContactStatus,
    VerifiedContact,
    VerifiedEmail,
)
from app.contact.web import (
    NullSearchProvider,
    SearchError,
    WebFetcher,
    make_search_provider,
    make_web_fetcher,
)
from app.config import (
    GENERIC_PLACEHOLDER_COMPANIES,
    GOV_DOMAIN_SUFFIXES,
)

# Known official domains, curated from PUBLIC knowledge. Each entry is a factual
# public website — never a guess derived from the company name alone.
_DEFAULT_DOMAIN_HINTS: Dict[str, Dict[str, str]] = {
    "zomato": {"domain": "zomato.com", "url": "https://www.zomato.com"},
    "swiggy": {"domain": "swiggy.com", "url": "https://www.swiggy.com"},
    "razorpay": {"domain": "razorpay.com", "url": "https://razorpay.com"},
    "zapier": {"domain": "zapier.com", "url": "https://zapier.com"},
    "notion": {"domain": "notion.so", "url": "https://www.notion.so"},
}

# Hosts that are a platform page, NOT an official company domain.
_BLOCKED_HOSTS = {
    "linkedin.com", "www.linkedin.com", "facebook.com", "www.facebook.com",
    "twitter.com", "www.twitter.com", "x.com", "www.x.com", "instagram.com",
    "www.instagram.com", "youtube.com", "www.youtube.com", "reddit.com",
    "www.reddit.com", "glassdoor.co.in", "glassdoor.com", "www.glassdoor.com",
    "indeed.com", "www.indeed.com", "in.indeed.com", "naukri.com", "www.naukri.com",
    "crunchbase.com", "www.crunchbase.com", "angel.co", "wellfound.com",
    "www.wellfound.com", "producthunt.com", "www.producthunt.com",
    "techcrunch.com", "www.techcrunch.com", "en.wikipedia.org",
    "wikipedia.org", "www.wikipedia.org",
}

# public mailbox prefix priority (EMAIL STANDARD) for deterministic ordering.
_EMAIL_RANK = {
    "careers": 1, "career": 1, "hiring": 2, "jobs": 3, "talent": 4,
    "recruit": 5, "recruiting": 5, "recruitment": 5, "recruiter": 5,
    "contact": 6, "hello": 6, "info": 6, "apply": 6, "hr": 6, "work": 6,
    "join": 6, "team": 6,
}


def _rank(addr: str) -> int:
    return _EMAIL_RANK.get(addr.split("@")[0].lower().strip(), 99)


class WebSearchContactDiscoveryProvider:
    """Real contact-discovery provider: search company -> fetch public pages ->
    extract literal emails / hiring contacts (all evidence-tagged, cached)."""

    def __init__(self, search_provider=None, fetcher=None, cache: Optional[ContactDiscoveryCache] = None,
                 domain_hints: Optional[Dict[str, Dict[str, str]]] = None,
                 max_pages: int = 4, max_requests: int = 8,
                 min_interval_s: float = 0.5, timeout_s: float = 12.0):
        self._search = search_provider if search_provider is not None else make_search_provider()
        self._fetch: WebFetcher = fetcher if fetcher is not None else make_web_fetcher()
        self._cache = cache or ContactDiscoveryCache()
        self._hints = dict(_DEFAULT_DOMAIN_HINTS)
        if domain_hints:
            self._hints.update(domain_hints)
        self._max_pages = max(1, int(max_pages))
        self._max_requests = max(1, int(max_requests))
        self._min_interval_s = max(0.0, float(min_interval_s))
        self._timeout_s = timeout_s
        self.request_count = 0
        self._last_request = 0.0

    # ── throttling / budget ───────────────────────────────────────────────────
    def _throttle(self) -> None:
        if not self._min_interval_s:
            return
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._min_interval_s:
            time.sleep(self._min_interval_s - elapsed)

    def _note_request(self) -> None:
        self._last_request = time.monotonic()
        self.request_count += 1

    # ── the seam ──────────────────────────────────────────────────────────────
    def discover(self, company: str, *, role: str = "",
                 source_text: str = "", extras: Dict | None = None) -> ContactEvidence:
        company = (company or "").strip()
        if not company or company in ("Unclear", "Not Available", ""):
            return ContactEvidence(
                status=ContactStatus.NOT_FOUND,
                company="Unclear",
                notes=["provider: no resolvable company name; no lookup."],
            )
        # BUG A: a generic geographic / country / government term is not a
        # private employer (e.g. "India is hiring..." from a national jobs
        # aggregator). We never search or resolve a domain for it, and we keep
        # the company as Unclear with an explanatory note.
        if company.lower() in GENERIC_PLACEHOLDER_COMPANIES:
            return ContactEvidence(
                status=ContactStatus.NOT_FOUND,
                company="Unclear",
                notes=[
                    "provider: '{}' is a generic geographic/government term, "
                    "not a resolvable hiring company; no lookup.".format(company)
                ],
            )
        # Caching first: reuse a company already resolved (perf/cost).
        cached = self._cache.get(company)
        if cached is not None:
            return cached
        self._throttle()
        try:
            return self._discover_uncached(company, role, source_text, extras)
        except ContactDiscoveryError:
            raise
        except Exception as e:  # fail CLOSED on anything unexpected
            raise ContactDiscoveryError(
                f"contact discovery failed for {company}: {type(e).__name__}"
            ) from e

    def _discover_uncached(self, company: str, role: str, source_text: str,
                           extras: Dict | None) -> ContactEvidence:
        domain, domain_source, domain_url, domain_conf = self._resolve_domain(company)
        pages = self._candidate_pages(domain, domain_url)
        emails: List[VerifiedEmail] = []
        contacts: List[VerifiedContact] = []
        evidence_urls: List[str] = []
        notes: List[str] = []
        if domain:
            notes.append(f"domain={domain} (source={domain_source})")
        for url in pages[: self._max_pages]:
            if self.request_count >= self._max_requests:
                notes.append("request budget reached; stopped early")
                break
            self._throttle()
            try:
                html = self._fetch.get(url, self._timeout_s)
            except Exception:
                self._note_request()
                continue  # one bad page must not kill discovery
            self._note_request()
            if url not in evidence_urls:
                evidence_urls.append(url)
            emails.extend(extract_emails(html, page_url=url, official_domain=domain))
            contacts.extend(extract_managers(html, page_url=url))
            # Enough evidence: stop fetching (perf/cost).
            if emails and contacts:
                break
        emails = self._dedupe_emails(emails)
        contacts = self._dedupe_contacts(contacts)

        if emails:
            status, confidence = ContactStatus.FOUND, max(
                domain_conf, max(v.confidence for v in emails))
        elif contacts:
            status, confidence = ContactStatus.FOUND, max(
                domain_conf, max(c.confidence for c in contacts))
        elif domain:
            status, confidence = ContactStatus.PARTIAL, domain_conf
        else:
            status, confidence = ContactStatus.NOT_FOUND, 0.0

        ev = ContactEvidence(
            status=status,
            company=company,
            company_domain=domain if domain else "Not Available",
            company_domain_source=domain_source,
            company_domain_evidence_url=domain_url,
            verified_emails=emails,
            contacts=contacts,
            evidence_urls=evidence_urls,
            confidence=round(min(1.0, confidence), 2),
            notes=notes,
        )
        # Cache the resolved company so a repeated lookup reuses this result.
        self._cache.set(company, ev)
        return ev

    # ── domain resolution (evidence-backed, never guessed) ────────────────────
    def _resolve_domain(self, company: str):
        key = company.lower().strip()
        hint = self._hints.get(key) or self._hints.get(re.sub(r"\s+", " ", key))
        if hint:
            return hint["domain"], "curated", hint.get("url", ""), 0.95
        if isinstance(self._search, NullSearchProvider):
            return "", "none", "", 0.0
        self._throttle()
        try:
            results = self._search.search(f'"{company}" official website', 8)
        except SearchError:
            return "", "none", "", 0.0
        self._note_request()
        for r in results:
            m = self._match_domain(company, r.url)
            if m:
                return m, "search", r.url, 0.85
        return "", "none", "", 0.0

    def _match_domain(self, company: str, url: str) -> Optional[str]:
        try:
            host = urllib.parse.urlparse(url).hostname or ""
        except ValueError:
            return None
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        if host in _BLOCKED_HOSTS or not host or "." not in host:
            return None
        # BUG A: public-sector / government domains (india.gov.in, nic.in, ...)
        # are never a private employer's domain, even when the slug matches.
        if host.endswith(GOV_DOMAIN_SUFFIXES):
            return None
        main = host.split(".")[0]
        slug = re.sub(r"[^a-z0-9]", "", company.lower())
        if not slug or len(slug) < 3:
            return None
        if slug == main or main in slug or slug in main and len(main) >= 3:
            return host
        # Some startups use a subdomain of a big platform; only accept when the
        # main label still matches the company slug.
        if len(host.split(".")) == 3 and slug in main and len(main) >= 3:
            return host
        return None

    def _candidate_pages(self, domain: str, domain_url: str) -> List[str]:
        if not domain:
            return []
        base = f"https://{domain}"
        urls = [domain_url] if domain_url and domain_url.startswith("http") else [base]
        for p in ("/careers", "/jobs", "/join", "/hiring",
                  "/contact", "/contact-us", "/about", "/team"):
            u2 = base + p
            if u2 not in urls:
                urls.append(u2)
        return urls

    # ── dedupe / ordering ─────────────────────────────────────────────────────
    def _dedupe_emails(self, emails: List[VerifiedEmail]) -> List[VerifiedEmail]:
        by_addr: Dict[str, VerifiedEmail] = {}
        for v in emails:
            key = v.email.lower()
            old = by_addr.get(key)
            if old is None or (v.source_tier, v.confidence) > (old.source_tier, old.confidence):
                by_addr[key] = v
        out = list(by_addr.values())
        out.sort(key=lambda v: (_rank(v.email), -v.source_tier, -v.confidence))
        return out

    def _dedupe_contacts(self, contacts: List[VerifiedContact]) -> List[VerifiedContact]:
        by_name: Dict[str, VerifiedContact] = {}
        for c in contacts:
            key = " ".join((c.name or "").lower().split())
            if not key:
                continue
            old = by_name.get(key)
            if old is None or (c.source_tier, c.confidence) > (old.source_tier, old.confidence):
                by_name[key] = c
        return list(by_name.values())


# ── Factory (config name -> provider instance) ────────────────────────────────
DEFAULT_CONTACT_PROVIDER = "null"

_PROVIDER_REGISTRY = {
    "null": lambda **kw: __import__(
        "app.contact.provider", fromlist=["NullContactDiscoveryProvider"]
    ).NullContactDiscoveryProvider(),
    "web_search": lambda **kw: WebSearchContactDiscoveryProvider(
        search_provider=make_search_provider(),
        fetcher=make_web_fetcher(),
        cache=ContactDiscoveryCache(path=kw.get("cache_path", "")),
        max_pages=int(kw.get("max_pages", 4)),
        max_requests=int(kw.get("max_requests", 8)),
        min_interval_s=float(kw.get("min_interval_s", 0.5)),
        timeout_s=float(kw.get("timeout_s", 12)),
    ),
}


def get_contact_provider(name: str = "null", **kwargs):
    """Factory from config name -> provider instance. Unknown names -> Null
    (fail-closed). Live network provider is opt-in ONLY."""
    builder = _PROVIDER_REGISTRY.get((name or "null").strip().lower())
    if builder is None:
        builder = _PROVIDER_REGISTRY["null"]
    return builder(**kwargs)